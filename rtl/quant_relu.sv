// -----------------------------------------------------------------------------
// quant_relu.sv - AXI4-Stream requantization stage: INT32 -> (ReLU) -> INT8.
//
// Sits behind sa_top: one input beat = one row of N accumulators (AW bits, signed),
// one output beat = the same row as N INT8 values. Per lane the result is
//
//     y = saturate_to_[-128,127]( floor( (relu(x) + 2^(shift-1)) / 2^shift ) )
//
// (requantization scale 2^-shift, round half up; shift = 0 means no scaling), computed
// WITHOUT a wide adder. Because floor((v + 2^(s-1)) / 2^s) = floor(v / 2^s) + v[s-1], the
// rounding is a carry-in of one bit, and saturation can be decided before it is added:
//
//   S1: ReLU, then ONE arithmetic shift of {v, 0} by `shift`: gives sh = floor(v / 2^s) in
//       the upper bits and the round bit rb = v[s-1] as the extra LSB (rb = 0 for shift 0)
//   S2: saturation flags from sh:  hi = (sh >= 127), lo = (sh < -128);  keep sh[7:0] and rb
//   S3: y = hi ? 127 : lo ? -128 : sh[7:0] + rb      (sh <= 126 here, so the 8-bit add
//       cannot overflow; sh >= 127 saturates for either value of rb)
//
//   cfg_relu / cfg_shift are used in S1, i.e. sampled when a beat enters the stage, so a
//   change right after the last beat was accepted does not affect beats in the pipeline.
//
// STAGES = number of pipeline registers = latency in cycles, full throughput for every value:
//   1:  [S1 S2 S3] R          2:  [S1] R [S2 S3] R          3:  [S1] R [S2] R [S3] R
// The whole pipeline advances together (adv = !m_axis_tvalid || m_axis_tready): when the
// output is stalled by back-pressure every register holds, so the output is stable, like
// the global `en` of sa_top. s_axis_tready = adv.
//
// Golden model: tb/common/sa_utils.py:quantize(). Formal: the properties at the end prove
// the whole lane function against an independent 64-bit specification.
// -----------------------------------------------------------------------------
module quant_relu #(
    parameter int N      = 4,
    parameter int AW     = 32,          // accumulator (input) width
    parameter int DW     = 8,           // output width
    parameter int STAGES = 1            // 1..3 pipeline registers (= latency in cycles)
) (
    input  logic              clk,
    input  logic              rst_n,

    input  logic [4:0]        cfg_shift,
    input  logic              cfg_relu,

    input  logic              s_axis_tvalid,
    output logic              s_axis_tready,
    input  logic [N*AW-1:0]   s_axis_tdata,
    input  logic              s_axis_tlast,

    output logic              m_axis_tvalid,
    input  logic              m_axis_tready,
    output logic [N*DW-1:0]   m_axis_tdata,
    output logic              m_axis_tlast
);

    localparam int ZW = AW + 2;                  // {sh (AW+1 bits), rb}: result of S1
    localparam int FW = DW + 3;                  // {hi, lo, sh[DW-1:0], rb}: result of S2
    localparam logic signed [AW:0] SAT_HI = (AW+1)'((1 << (DW-1)) - 1);  //  127
    localparam logic signed [AW:0] SAT_LO = (AW+1)'(-(1 << (DW-1)));     // -128

    // S2: saturation flags of the floored value, plus the bits S3 needs
    function automatic logic [FW-1:0] flags(input logic [ZW-1:0] zs);
        logic signed [AW:0] sh;
        sh = zs[ZW-1:1];
        flags = {(sh >= SAT_HI), (sh < SAT_LO), sh[DW-1:0], zs[0]};
    endfunction

    // S3: select the saturated value or add the round bit to the 8-bit result
    function automatic logic signed [DW-1:0] fin(input logic [FW-1:0] f);
        fin = f[FW-1] ? {1'b0, {(DW-1){1'b1}}}
            : f[FW-2] ? {1'b1, {(DW-1){1'b0}}}
            :           f[DW:1] + DW'(f[0]);
    endfunction

    logic adv;
    assign adv           = !m_axis_tvalid || m_axis_tready;
    assign s_axis_tready = adv;

    genvar i;

    // ------------------------------------------------------ S1: ReLU, shift, round bit
    logic [N*ZW-1:0] c1_zs;
    generate
        for (i = 0; i < N; i = i + 1) begin : g_s1
            logic signed [AW-1:0] x;
            logic signed [AW:0]   xr;       // after ReLU, sign-extended by one bit
            logic signed [AW+1:0] z;        // {xr, 0}: the extra LSB ends up holding the round bit
            logic signed [AW+1:0] zs;

            assign x  = s_axis_tdata[i*AW +: AW];
            assign xr = (cfg_relu && x[AW-1]) ? '0 : {x[AW-1], x};
            assign z  = {xr, 1'b0};
            assign zs = z >>> cfg_shift;
            assign c1_zs[i*ZW +: ZW] = zs;
        end
    endgenerate

    // register after S1 (STAGES >= 2), otherwise a wire
    logic [N*ZW-1:0] p1_zs;
    logic            p1_valid, p1_last;
    generate
        if (STAGES >= 2) begin : g_r1
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    p1_valid <= 1'b0;
                    p1_zs    <= '0;
                    p1_last  <= 1'b0;
                end else if (adv) begin
                    p1_valid <= s_axis_tvalid;
                    p1_zs    <= c1_zs;
                    p1_last  <= s_axis_tlast;
                end
            end
        end else begin : g_c1
            assign p1_valid = s_axis_tvalid;
            assign p1_zs    = c1_zs;
            assign p1_last  = s_axis_tlast;
        end
    endgenerate

    // ------------------------------------------------------------ S2: saturation flags
    logic [N*FW-1:0] c2_f;
    generate
        for (i = 0; i < N; i = i + 1) begin : g_s2
            assign c2_f[i*FW +: FW] = flags(p1_zs[i*ZW +: ZW]);
        end
    endgenerate

    // register after S2 (STAGES >= 3), otherwise a wire
    logic [N*FW-1:0] p2_f;
    logic            p2_valid, p2_last;
    generate
        if (STAGES >= 3) begin : g_r2
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    p2_valid <= 1'b0;
                    p2_f     <= '0;
                    p2_last  <= 1'b0;
                end else if (adv) begin
                    p2_valid <= p1_valid;
                    p2_f     <= c2_f;
                    p2_last  <= p1_last;
                end
            end
        end else begin : g_c2
            assign p2_valid = p1_valid;
            assign p2_f     = c2_f;
            assign p2_last  = p1_last;
        end
    endgenerate

    // ------------------------------------------------- S3: finish, output register
    logic [N*DW-1:0] q_data;
    generate
        for (i = 0; i < N; i = i + 1) begin : g_s3
            assign q_data[i*DW +: DW] = fin(p2_f[i*FW +: FW]);
        end
    endgenerate

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            m_axis_tvalid <= 1'b0;
            m_axis_tdata  <= '0;
            m_axis_tlast  <= 1'b0;
        end else if (adv) begin
            m_axis_tvalid <= p2_valid;
            if (p2_valid) begin                 // data only changes with a valid beat
                m_axis_tdata <= q_data;
                m_axis_tlast <= p2_last;
            end
        end
    end

    // ------------------------------------------------------------ formal props
`ifdef FORMAL
    logic f_past_valid = 1'b0;
    always_ff @(posedge clk) f_past_valid <= 1'b1;

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

    // P1: output is stable while stalled by back-pressure
    always_ff @(posedge clk) begin
        if (f_past_valid && $past(rst_n) && rst_n && $past(m_axis_tvalid && !m_axis_tready)) begin
            assert (m_axis_tvalid);
            assert (m_axis_tdata == $past(m_axis_tdata));
            assert (m_axis_tlast == $past(m_axis_tlast));
        end
    end

    // Lane function S3(S2(S1(x))) of lane 0, as one combinational chain (every lane is the same
    // generate block; the pipeline registers only move these values).
    logic signed [AW-1:0] f_x;
    logic signed [DW-1:0] f_y;
    assign f_x = s_axis_tdata[0 +: AW];
    assign f_y = fin(flags(c1_zs[0 +: ZW]));

    always_comb begin
        // P2: ReLU - a negative accumulator gives 0, and the output is never negative
        if (cfg_relu && f_x < 0) assert (f_y == 0);
        if (cfg_relu)            assert (f_y >= 0);
        // P3: no scaling and in range -> identity (with ReLU: for non-negative values)
        if (cfg_shift == 0 && f_x >= -128 && f_x <= 127 && (!cfg_relu || f_x >= 0))
            assert (f_y == f_x[DW-1:0]);
        // P4: saturation without scaling
        if (cfg_shift == 0 && f_x > 127)                 assert (f_y == 127);
        if (cfg_shift == 0 && !cfg_relu && f_x < -128)   assert (f_y == -128);
    end

    // P5: any shift. Independent spec in 64-bit arithmetic (it shares neither the shifter nor
    // the round-bit trick of the datapath): with v = relu(x) + 2^(shift-1), the result y satisfies
    //     y > -128  ->  y * 2^shift       <= v      (not rounded too low)
    //     y <  127  ->  v < (y + 1) * 2^shift       (not rounded too high)
    // so an unsaturated y is exactly floor(v / 2^shift), and a saturated one is on the
    // correct side. This pins down rounding, shifting and saturation for every shift.
    logic signed [63:0] f_v, f_y64;
    assign f_v   = ((cfg_relu && f_x < 0) ? 64'sd0 : 64'(f_x))
                 + ((cfg_shift == 0) ? 64'sd0 : (64'sd1 <<< (cfg_shift - 5'd1)));
    assign f_y64 = 64'(f_y);
    always_comb begin
        if (f_y > -128) assert ((f_y64 <<< cfg_shift) <= f_v);
        if (f_y <  127) assert (f_v < ((f_y64 + 64'sd1) <<< cfg_shift));
    end

    // reachability: a beat passes through
    always_ff @(posedge clk) if (rst_n) cover (m_axis_tvalid && m_axis_tready && m_axis_tlast);
`endif

endmodule
