// -----------------------------------------------------------------------------
// sa_layer.sv - "Layer engine": systolic GEMM + INT32 -> ReLU -> INT8 requantization.
//
// sa_top computes one N x N tile C = A * B in INT32; quant_relu turns each output row
// into N INT8 values (optional ReLU, right shift with rounding, saturation). The AXI4-
// Stream interfaces are those of sa_top, except that m_axis_tdata is N*8 bits wide.
// Latency: sa_top's latency + QSTAGES cycles (the quant_relu pipeline).
// -----------------------------------------------------------------------------
module sa_layer #(
    parameter int N    = 4,
    parameter int DW   = 8,
    parameter int AW   = 32,
    parameter int CW   = 32,
    parameter int PIPE = 1,
    parameter int QSTAGES = 1        // quantizer pipeline registers (= its latency); 1 is enough, see README
) (
    input  logic              clk,
    input  logic              rst_n,

    input  logic [4:0]        cfg_shift,      // requantization: >> cfg_shift, round half up
    input  logic              cfg_relu,       // 1: ReLU before requantization

    input  logic              s_axis_tvalid,
    output logic              s_axis_tready,
    input  logic [2*N*DW-1:0] s_axis_tdata,
    input  logic              s_axis_tlast,

    output logic              m_axis_tvalid,
    input  logic              m_axis_tready,
    output logic [N*DW-1:0]   m_axis_tdata,   // one row of INT8 results per beat
    output logic              m_axis_tlast,

    output logic              busy,
    output logic [CW-1:0]     last_job_cycles
);

    logic              c_tvalid, c_tready, c_tlast;
    logic [N*AW-1:0]   c_tdata;

    sa_top #(.N(N), .DW(DW), .AW(AW), .CW(CW), .PIPE(PIPE)) u_gemm (
        .clk(clk), .rst_n(rst_n),
        .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
        .s_axis_tdata(s_axis_tdata),   .s_axis_tlast(s_axis_tlast),
        .m_axis_tvalid(c_tvalid), .m_axis_tready(c_tready),
        .m_axis_tdata(c_tdata),   .m_axis_tlast(c_tlast),
        .busy(busy), .last_job_cycles(last_job_cycles)
    );

    quant_relu #(.N(N), .AW(AW), .DW(DW), .STAGES(QSTAGES)) u_quant (
        .clk(clk), .rst_n(rst_n),
        .cfg_shift(cfg_shift), .cfg_relu(cfg_relu),
        .s_axis_tvalid(c_tvalid), .s_axis_tready(c_tready),
        .s_axis_tdata(c_tdata),   .s_axis_tlast(c_tlast),
        .m_axis_tvalid(m_axis_tvalid), .m_axis_tready(m_axis_tready),
        .m_axis_tdata(m_axis_tdata),   .m_axis_tlast(m_axis_tlast)
    );

endmodule
