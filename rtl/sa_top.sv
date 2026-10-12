// -----------------------------------------------------------------------------
// sa_top.sv - AXI4-Stream wrapped N x N INT8 systolic-array GEMM engine.
//
// Computes one N x N output tile:   C = A * B
//      A : N x K   (signed DW-bit)
//      B : K x N   (signed DW-bit)
//      C : N x N   (signed AW-bit)
//
// ---- Input stream (s_axis) : K beats, outer-product order -------------------
//   beat k carries column k of A and row k of B:
//     s_axis_tdata[i*DW +: DW]          = A[i][k]   (i = 0..N-1)
//     s_axis_tdata[(N+j)*DW +: DW]      = B[k][j]   (j = 0..N-1)
//     s_axis_tlast = 1 on the last beat (k = K-1)
//
// ---- Output stream (m_axis) : N beats, one row of C per beat ----------------
//     m_axis_tdata[j*AW +: AW]          = C[r][j]   (beat r = 0..N-1)
//     m_axis_tlast = 1 on the last row
//
// ---- FSM ---------------------------------------------------------------------
//   CLEAR  : 1 cycle, clears accumulators + skew registers
//   LOAD   : s_axis_tready = 1; array advances on every accepted beat
//   DRAIN  : feeds zeros for DRAIN_CYC cycles so the last beat reaches PE(N-1,N-1)
//   OUTPUT : streams the N result rows (holds data stable under back-pressure)
//
// Latency with no stalls (cycles from entering LOAD to last output handshake):
//   K + DRAIN_CYC + N  =  K + 3N - 2   (DRAIN_CYC = 2N - 2 for N > 1; N = 1 uses 1)
// last_job_cycles reports the measured value for the previous job.
// -----------------------------------------------------------------------------
module sa_top #(
    parameter int N  = 4,
    parameter int DW = 8,
    parameter int AW = 32,
    parameter int CW = 32          // width of the performance cycle counter
) (
    input  logic              clk,
    input  logic              rst_n,

    // AXI4-Stream slave (operands in)
    input  logic              s_axis_tvalid,
    output logic              s_axis_tready,
    input  logic [2*N*DW-1:0] s_axis_tdata,
    input  logic              s_axis_tlast,

    // AXI4-Stream master (results out)
    output logic              m_axis_tvalid,
    input  logic              m_axis_tready,
    output logic [N*AW-1:0]   m_axis_tdata,
    output logic              m_axis_tlast,

    // status / performance
    output logic              busy,
    output logic [CW-1:0]     last_job_cycles
);

    // minimum drain: last beat needs N-1 skew cycles + N-1 hops to reach PE(N-1,N-1)
    localparam int DRAIN_CYC = (N > 1) ? 2 * N - 2 : 1;
    localparam int DCW       = $clog2(DRAIN_CYC + 1);
    localparam int RCW       = (N > 1) ? $clog2(N) : 1;

    localparam logic [DCW-1:0] DRAIN_LAST = DCW'(DRAIN_CYC - 1);
    localparam logic [RCW-1:0] ROW_LAST   = RCW'(N - 1);

    // FSM encoding (plain localparams for maximum tool portability)
    localparam logic [1:0] S_CLEAR  = 2'd0;
    localparam logic [1:0] S_LOAD   = 2'd1;
    localparam logic [1:0] S_DRAIN  = 2'd2;
    localparam logic [1:0] S_OUTPUT = 2'd3;

    logic [1:0]      state;
    logic [DCW-1:0]  drain_cnt;
    logic [RCW-1:0]  row_idx;
    logic [CW-1:0]   cyc_cnt;

    logic            in_fire;
    logic            out_fire;
    logic            en;
    logic            clr;

    logic [N*DW-1:0]   a_raw, b_raw;     // un-skewed operands of this cycle
    logic [N*DW-1:0]   a_skew, b_skew;   // skewed operands into the array
    logic [N*N*AW-1:0] acc_flat;

    // ------------------------------------------------------------------ control
    assign s_axis_tready = (state == S_LOAD);
    assign in_fire       = s_axis_tvalid && s_axis_tready;
    assign out_fire      = m_axis_tvalid && m_axis_tready;

    assign en  = in_fire || (state == S_DRAIN);
    assign clr = (state == S_CLEAR);

    // zeros are fed during DRAIN (they add nothing to the accumulators)
    assign a_raw = in_fire ? s_axis_tdata[0      +: N*DW] : '0;
    assign b_raw = in_fire ? s_axis_tdata[N*DW   +: N*DW] : '0;

    assign busy  = (state != S_LOAD) || (cyc_cnt != '0);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state           <= S_CLEAR;
            drain_cnt       <= '0;
            row_idx         <= '0;
            cyc_cnt         <= '0;
            last_job_cycles <= '0;
        end else begin
            case (state)
                S_CLEAR: begin
                    drain_cnt <= '0;
                    row_idx   <= '0;
                    cyc_cnt   <= '0;
                    state     <= S_LOAD;
                end

                S_LOAD: begin
                    // start counting at the first accepted beat
                    if (in_fire || cyc_cnt != '0) cyc_cnt <= cyc_cnt + 1'b1;
                    if (in_fire && s_axis_tlast) state <= S_DRAIN;
                end

                S_DRAIN: begin
                    cyc_cnt <= cyc_cnt + 1'b1;
                    if (drain_cnt == DRAIN_LAST) begin
                        drain_cnt <= '0;
                        state     <= S_OUTPUT;
                    end else begin
                        drain_cnt <= drain_cnt + 1'b1;
                    end
                end

                S_OUTPUT: begin
                    cyc_cnt <= cyc_cnt + 1'b1;
                    if (out_fire) begin
                        if (row_idx == ROW_LAST) begin
                            last_job_cycles <= cyc_cnt + 1'b1;
                            state           <= S_CLEAR;
                        end else begin
                            row_idx <= row_idx + 1'b1;
                        end
                    end
                end

                default: state <= S_CLEAR;
            endcase
        end
    end

    // ------------------------------------------------------------- datapath
    skew_buffer #(.N(N), .DW(DW)) u_skew_a (
        .clk (clk), .rst_n (rst_n), .en (en), .clr (clr),
        .din (a_raw), .dout (a_skew)
    );

    skew_buffer #(.N(N), .DW(DW)) u_skew_b (
        .clk (clk), .rst_n (rst_n), .en (en), .clr (clr),
        .din (b_raw), .dout (b_skew)
    );

    systolic_array #(.N(N), .DW(DW), .AW(AW)) u_array (
        .clk      (clk),
        .rst_n    (rst_n),
        .en       (en),
        .clr      (clr),
        .a_left   (a_skew),
        .b_top    (b_skew),
        .acc_flat (acc_flat)
    );

    // --------------------------------------------------------------- output
    assign m_axis_tvalid = (state == S_OUTPUT);
    assign m_axis_tdata  = acc_flat[row_idx*N*AW +: N*AW];
    assign m_axis_tlast  = (state == S_OUTPUT) && (row_idx == ROW_LAST);

    // ------------------------------------------------------------ formal props
`ifdef FORMAL
    logic f_past_valid = 1'b0;
    always_ff @(posedge clk) f_past_valid <= 1'b1;

    // start in reset
    always_comb if (!f_past_valid) assume (!rst_n);

    // upstream obeys AXI4-Stream: once valid, hold valid/data/last until ready
    always_ff @(posedge clk) begin
        if (f_past_valid && $past(rst_n) && rst_n &&
            $past(s_axis_tvalid && !s_axis_tready)) begin
            assume (s_axis_tvalid);
            assume (s_axis_tdata == $past(s_axis_tdata));
            assume (s_axis_tlast == $past(s_axis_tlast));
        end
    end

    always_ff @(posedge clk) begin
        if (f_past_valid && $past(rst_n) && rst_n) begin
            // P1: output is stable while stalled by back-pressure
            if ($past(m_axis_tvalid && !m_axis_tready)) begin
                assert (m_axis_tvalid);
                assert (m_axis_tdata == $past(m_axis_tdata));
                assert (m_axis_tlast == $past(m_axis_tlast));
            end
        end
    end

    always_comb begin
        if (rst_n) begin
            // P2: never accept input while producing output
            assert (!(s_axis_tready && m_axis_tvalid));
            // P3: tlast only accompanies a valid beat
            if (m_axis_tlast) assert (m_axis_tvalid);
            // P4: row index stays in range
            assert (row_idx <= ROW_LAST);
        end
    end

    // reachability: a full job completes
    always_ff @(posedge clk) begin
        if (rst_n) cover (out_fire && m_axis_tlast);
    end
`endif

endmodule
