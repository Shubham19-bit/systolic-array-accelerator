// -----------------------------------------------------------------------------
// skew_buffer.sv - Delays lane i by i enabled cycles.
//
// A systolic array needs its inputs "skewed" (staggered) so that A[i][k] and
// B[k][j] meet in PE(i,j) on the same cycle. Lane 0 passes straight through,
// lane 1 is delayed by 1 cycle, lane 2 by 2 cycles, ... lane N-1 by N-1 cycles.
//
// All delay registers advance only when en = 1, so a stall freezes the whole
// pipeline consistently with the PE array.
//
// Each lane's delay line is a packed shift register (newest sample in the
// lowest DW bits, oldest in the highest DW bits).
// -----------------------------------------------------------------------------
module skew_buffer #(
    parameter int N  = 4,
    parameter int DW = 8
) (
    input  logic            clk,
    input  logic            rst_n,
    input  logic            en,
    input  logic            clr,
    input  logic [N*DW-1:0] din,   // lane i at din[i*DW +: DW]
    output logic [N*DW-1:0] dout   // lane i delayed by i cycles
);

    // lane 0: no delay
    assign dout[0 +: DW] = din[0 +: DW];

    genvar i;
    generate
        for (i = 1; i < N; i = i + 1) begin : g_lane
            logic [i*DW-1:0] sr;

            if (i == 1) begin : g_d1
                always_ff @(posedge clk or negedge rst_n) begin
                    if (!rst_n)   sr <= '0;
                    else if (clr) sr <= '0;
                    else if (en)  sr <= din[i*DW +: DW];
                end
            end else begin : g_dn
                always_ff @(posedge clk or negedge rst_n) begin
                    if (!rst_n)   sr <= '0;
                    else if (clr) sr <= '0;
                    else if (en)  sr <= {sr[(i-1)*DW-1:0], din[i*DW +: DW]};
                end
            end

            // oldest sample = delayed by i cycles
            assign dout[i*DW +: DW] = sr[i*DW-1 -: DW];
        end
    endgenerate

endmodule
