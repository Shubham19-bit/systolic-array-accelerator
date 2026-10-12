// -----------------------------------------------------------------------------
// sa_gemm.sv - Tiled GEMM engine: C[M x Nc] = A[M x K] * B[K x Nc] for matrices larger
//              than the N x N array, with on-chip operand buffers.
//
// Why: sa_top re-streams both operands for every output tile, so its external traffic is
// mb*nb*2*K*N bytes (mb = ceil(M/N), nb = ceil(Nc/N)). Here A and B are written into two
// on-chip memories ONCE ((mb + nb)*K*N bytes) and every tile reads them locally.
//
// ---- Memory layout (the host / a DMA writes one N-byte word per cycle) -----------
//   A_MEM[ib*K + k] : lane i = A[ib*N + i][k]   (column k of row block ib)
//   B_MEM[jb*K + k] : lane j = B[k][jb*N + j]   (row k of column block jb)
//   M and Nc are padded with zeros up to multiples of N by the host.
//
// ---- Operation -----------------------------------------------------------------
//   1. write A_MEM and B_MEM (not while busy);  2. set cfg_k / cfg_mb / cfg_nb, pulse start.
//   Tiles run in the order ib = 0..mb-1 (outer), jb = 0..nb-1 (inner): tile (ib, jb) streams
//   K beats {B_MEM[jb*K+k], A_MEM[ib*K+k]} into sa_top, then N result rows come out. The
//   m_axis stream carries the tiles in that order, N rows (N*AW bits each) per tile;
//   m_axis_tlast marks the last row of the LAST tile. `done` pulses when it is accepted.
//
// ---- Feeding sa_top -------------------------------------------------------------
//   The memories have a 1-cycle read latency, so a 2-entry FIFO sits between them and
//   sa_top. A read is issued only if a FIFO slot will be free when the data returns
//   (occupancy + in-flight < 2, or a pop happens this cycle), which gives one beat per
//   cycle while sa_top is in LOAD, and lets the first beats of the next tile be fetched
//   while the current tile drains and is read out.
//
//   Latency, no stalls:  gemm_cycles = tiles * (K + DRAIN + N + 1) + 1, with
//   DRAIN = (2N-2, or 1 if N=1) + PIPE, i.e. tiles * tile_cycles() of analysis/perf_model.py.
//   gemm_cycles counts from the first cycle after `start` up to the last row handshake.
// -----------------------------------------------------------------------------
module sa_gemm #(
    parameter int N     = 4,
    parameter int DW    = 8,
    parameter int AW    = 32,
    parameter int CW    = 32,       // width of the performance counters
    parameter int PIPE  = 1,
    // Capacity: MAXMB*MAXK >= 2 and MAXNB*MAXK >= 2 (a one-word buffer has a zero-width address)
    parameter int MAXK  = 64,       // deepest K (beats per tile) the buffers can hold
    parameter int MAXMB = 4,        // at most MAXMB row blocks of A    (M  <= MAXMB*N)
    parameter int MAXNB = 4,        // at most MAXNB column blocks of B (Nc <= MAXNB*N)
    localparam int KW   = $clog2(MAXK + 1),
    localparam int MBW  = $clog2(MAXMB + 1),
    localparam int NBW  = $clog2(MAXNB + 1),
    localparam int A_AW = $clog2(MAXMB * MAXK),
    localparam int B_AW = $clog2(MAXNB * MAXK)
) (
    input  logic              clk,
    input  logic              rst_n,

    // job configuration, sampled on `start`: 1 <= cfg_k <= MAXK, 1 <= cfg_mb <= MAXMB, ...
    input  logic [KW-1:0]     cfg_k,
    input  logic [MBW-1:0]    cfg_mb,
    input  logic [NBW-1:0]    cfg_nb,
    input  logic              start,        // ignored while busy
    output logic              busy,
    output logic              done,         // one-cycle pulse with the last row handshake

    // operand memories: one N-byte word per write
    input  logic              a_we,
    input  logic [A_AW-1:0]   a_waddr,
    input  logic [N*DW-1:0]   a_wdata,
    input  logic              b_we,
    input  logic [B_AW-1:0]   b_waddr,
    input  logic [N*DW-1:0]   b_wdata,

    // result stream: N rows of N*AW bits per tile, tiles in (ib, jb) order
    output logic              m_axis_tvalid,
    input  logic              m_axis_tready,
    output logic [N*AW-1:0]   m_axis_tdata,
    output logic              m_axis_tlast,

    // performance counters
    output logic [CW-1:0]     gemm_cycles,  // cycles of the last finished job
    output logic [CW-1:0]     beats_fed     // operand beats read from the buffers this job
);

    // ------------------------------------------------------------------- state
    logic            run;
    logic [KW-1:0]   k_q;                  // configuration of the running job
    logic [MBW-1:0]  mb_q;
    logic [NBW-1:0]  nb_q;

    logic [KW-1:0]   k;                    // read-issue position: beat k of tile (ib, jb)
    logic [MBW-1:0]  ib;
    logic [NBW-1:0]  jb;
    logic [A_AW-1:0] a_base;               // = ib * K
    logic [B_AW-1:0] b_base;               // = jb * K
    logic            issue_done;           // all reads of the job have been issued

    logic            rd_pending;           // a read was issued last cycle: rdata is valid now
    logic            rd_last_q;            // ... and it was the last beat of a tile

    logic [1:0]          occ;              // FIFO occupancy (0..2)
    logic [2*N*DW-1:0]   f0, f1;
    logic                f0_last, f1_last;

    logic [MBW-1:0]  oi;                   // output tile position (tiles finished so far)
    logic [NBW-1:0]  oj;
    logic [CW-1:0]   cyc;

    // ---------------------------------------------------------------- memories
    logic [N*DW-1:0] a_rdata, b_rdata;
    logic            issue;
    logic [A_AW-1:0] a_raddr;
    logic [B_AW-1:0] b_raddr;

    assign a_raddr = a_base + A_AW'(k);
    assign b_raddr = b_base + B_AW'(k);

    sram_sp #(.DEPTH(MAXMB * MAXK), .WIDTH(N * DW)) u_a_mem (
        .clk(clk), .we(a_we), .waddr(a_waddr), .wdata(a_wdata),
        .re(issue), .raddr(a_raddr), .rdata(a_rdata)
    );

    sram_sp #(.DEPTH(MAXNB * MAXK), .WIDTH(N * DW)) u_b_mem (
        .clk(clk), .we(b_we), .waddr(b_waddr), .wdata(b_wdata),
        .re(issue), .raddr(b_raddr), .rdata(b_rdata)
    );

    // -------------------------------------------------------------- the array
    logic            sa_tready, c_tvalid, c_tlast;
    logic [CW-1:0]   unused_last_job_cycles;
    logic            unused_busy;

    sa_top #(.N(N), .DW(DW), .AW(AW), .CW(CW), .PIPE(PIPE)) u_sa (
        .clk(clk), .rst_n(rst_n),
        .s_axis_tvalid(occ != 2'd0), .s_axis_tready(sa_tready),
        .s_axis_tdata(f0),           .s_axis_tlast(f0_last),
        .m_axis_tvalid(c_tvalid),    .m_axis_tready(m_axis_tready),
        .m_axis_tdata(m_axis_tdata), .m_axis_tlast(c_tlast),
        .busy(unused_busy), .last_job_cycles(unused_last_job_cycles)
    );

    // ----------------------------------------------------------------- control
    logic pop, push, out_fire, last_tile;
    logic [2:0] used;

    assign pop       = (occ != 2'd0) && sa_tready;
    assign push      = rd_pending;
    assign used      = {1'b0, occ} + {2'b00, rd_pending};
    assign issue     = run && !issue_done && (used < 3'd2 || pop);

    assign out_fire  = c_tvalid && m_axis_tready;
    assign last_tile = (oi == mb_q - MBW'(1)) && (oj == nb_q - NBW'(1));

    assign m_axis_tvalid = c_tvalid;
    assign m_axis_tlast  = c_tlast && last_tile && run;
    assign busy          = run;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            run         <= 1'b0;
            done        <= 1'b0;
            issue_done  <= 1'b0;
            rd_pending  <= 1'b0;
            rd_last_q   <= 1'b0;
            k_q         <= '0;
            mb_q        <= '0;
            nb_q        <= '0;
            k           <= '0;
            ib          <= '0;
            jb          <= '0;
            a_base      <= '0;
            b_base      <= '0;
            oi          <= '0;
            oj          <= '0;
            cyc         <= '0;
            gemm_cycles <= '0;
            beats_fed   <= '0;
        end else begin
            done       <= 1'b0;
            rd_pending <= issue;          // a one-cycle pulse, also if run drops (issue is gated by run)
            if (start && !run) begin
                run        <= 1'b1;
                k_q        <= cfg_k;
                mb_q       <= cfg_mb;
                nb_q       <= cfg_nb;
                k          <= '0;
                ib         <= '0;
                jb         <= '0;
                a_base     <= '0;
                b_base     <= '0;
                issue_done <= 1'b0;
                oi         <= '0;
                oj         <= '0;
                cyc        <= '0;
                beats_fed  <= '0;
            end else if (run) begin
                cyc        <= cyc + 1'b1;
                if (issue) begin
                    rd_last_q <= (k == k_q - KW'(1));
                    beats_fed <= beats_fed + 1'b1;
                    if (k == k_q - KW'(1)) begin                 // last beat of tile (ib, jb)
                        k <= '0;
                        if (jb == nb_q - NBW'(1)) begin          // last column block
                            jb     <= '0;
                            b_base <= '0;
                            a_base <= a_base + A_AW'(k_q);
                            if (ib == mb_q - MBW'(1)) issue_done <= 1'b1;
                            else                      ib <= ib + 1'b1;
                        end else begin
                            jb     <= jb + 1'b1;
                            b_base <= b_base + B_AW'(k_q);
                        end
                    end else begin
                        k <= k + 1'b1;
                    end
                end
                if (out_fire && c_tlast) begin                   // a tile has been read out
                    if (oj == nb_q - NBW'(1)) begin
                        oj <= '0;
                        oi <= oi + 1'b1;
                    end else begin
                        oj <= oj + 1'b1;
                    end
                    if (last_tile) begin
                        run         <= 1'b0;
                        done        <= 1'b1;
                        gemm_cycles <= cyc + 1'b1;
                    end
                end
            end
        end
    end

    // ----------------------------------------------------- 2-entry operand FIFO
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            occ     <= 2'd0;
            f0      <= '0;
            f1      <= '0;
            f0_last <= 1'b0;
            f1_last <= 1'b0;
        end else begin
            case ({push, pop})
                2'b10: begin                                      // push only
                    if (occ == 2'd0) begin
                        f0 <= {b_rdata, a_rdata};  f0_last <= rd_last_q;
                    end else begin
                        f1 <= {b_rdata, a_rdata};  f1_last <= rd_last_q;
                    end
                    occ <= occ + 2'd1;
                end
                2'b01: begin                                      // pop only
                    f0 <= f1;  f0_last <= f1_last;
                    occ <= occ - 2'd1;
                end
                2'b11: begin                                      // pop and push
                    if (occ == 2'd1) begin
                        f0 <= {b_rdata, a_rdata};  f0_last <= rd_last_q;
                    end else begin
                        f0 <= f1;                  f0_last <= f1_last;
                        f1 <= {b_rdata, a_rdata};  f1_last <= rd_last_q;
                    end
                end
                default: ;
            endcase
        end
    end

    // ------------------------------------------------------------ formal props
`ifdef FORMAL
    logic f_past_valid = 1'b0;
    always_ff @(posedge clk) f_past_valid <= 1'b1;

    always_comb if (!f_past_valid) assume (!rst_n);

    // interface rules for the host: it only starts jobs the buffers can hold, and only while the
    // result stream is idle (the previous job's last row has been taken)
    always_comb begin
        assume (!(start && m_axis_tvalid));
        assume (cfg_k  >= 1 && cfg_k  <= MAXK);
        assume (cfg_mb >= 1 && cfg_mb <= MAXMB);
        assume (cfg_nb >= 1 && cfg_nb <= MAXNB);
    end

    always_comb begin
        if (rst_n) begin
            // G1: the 2-entry FIFO never overflows (occupancy + the read in flight <= 2)
            assert (occ <= 2'd2);
            assert ({1'b0, occ} + {2'b00, rd_pending} <= 3'd2);
            // G0: the latched job configuration is valid while running (loaded from the
            //     assumed-valid cfg_* inputs; needed so that G2 holds for every induction state)
            if (run) begin
                assert (k_q  >= 1 && k_q  <= MAXK);
                assert (mb_q >= 1 && mb_q <= MAXMB);
                assert (nb_q >= 1 && nb_q <= MAXNB);
            end
            // G2: address generation. The pointers are the products ib*K and jb*K, so every
            //     issued read is inside the buffers
            assert (b_base == B_AW'(jb * k_q) || !run);
            if (run && !issue_done) begin
                assert (a_base == A_AW'(ib * k_q));
                assert (ib < mb_q && jb < nb_q && k < k_q);
            end
            if (issue) begin
                assert (int'(a_raddr) < MAXMB * MAXK);
                assert (int'(b_raddr) < MAXNB * MAXK);
            end
            // G3: tile position of the output side stays inside the job
            if (run) assert (oi < mb_q && oj < nb_q);
            // G4: framing and idle state
            if (m_axis_tlast) assert (m_axis_tvalid && run);
            if (done)         assert (!busy);
        end
    end

    always_ff @(posedge clk) begin
        if (f_past_valid && $past(rst_n) && rst_n) begin
            // G5: done follows exactly the handshake of the last row of the last tile
            if (done) assert ($past(m_axis_tvalid && m_axis_tready && m_axis_tlast));
            if ($past(m_axis_tvalid && m_axis_tready && m_axis_tlast)) assert (done);
            // G6: the result stream is stable while stalled by back-pressure
            if ($past(m_axis_tvalid && !m_axis_tready)) begin
                assert (m_axis_tvalid);
                assert (m_axis_tdata == $past(m_axis_tdata));
                assert (m_axis_tlast == $past(m_axis_tlast));
            end
            // G7: start is ignored while busy: the job configuration does not change
            if ($past(run) && run) assert (k_q == $past(k_q) && mb_q == $past(mb_q) && nb_q == $past(nb_q));
        end
    end

    // reachability: a whole job completes
    always_ff @(posedge clk) if (rst_n) cover (done);
`endif

endmodule
