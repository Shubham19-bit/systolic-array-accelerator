// -----------------------------------------------------------------------------
// pe.sv - Output-stationary processing element (PE)
//
// Each PE multiplies the operand arriving from the left (a_in) with the operand
// arriving from the top (b_in), adds the product to its local accumulator, and
// forwards both operands (registered) to its right / bottom neighbour.
//
//   a_in --> [ acc += a_in * b_in ] --> a_out (to PE on the right)
//                   |
//                 b_out (to PE below)
//
// PIPE = 0: multiply and accumulate in one cycle (critical path: multiplier -> adder
//           -> acc register).
// PIPE = 1: the product is registered first (prod_r) and added to acc one cycle later,
//           so the multiplier and the adder are in different pipeline stages. The final
//           accumulator value is ready one enabled cycle later (sa_top adds 1 DRAIN cycle).
//
// en  : global stall/advance. When 0 every register holds its value (prod_r too).
// clr : synchronous clear of accumulator, forwarding registers and prod_r.
// -----------------------------------------------------------------------------
module pe #(
    parameter int DW   = 8,   // operand width (signed INT8 by default)
    parameter int AW   = 32,  // accumulator width
    parameter int PIPE = 1    // 1: register the product before accumulating
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 en,
    input  logic                 clr,
    input  logic signed [DW-1:0] a_in,
    input  logic signed [DW-1:0] b_in,
    output logic signed [DW-1:0] a_out,
    output logic signed [DW-1:0] b_out,
    output logic signed [AW-1:0] acc
);

    logic signed [2*DW-1:0] prod;        // product of this cycle's operands
    logic signed [2*DW-1:0] prod_acc;    // the product that is added to acc this cycle
    logic signed [AW-1:0]   prod_ext;

    assign prod = a_in * b_in;

    generate
        if (PIPE != 0) begin : g_pipe
            logic signed [2*DW-1:0] prod_r;
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n)   prod_r <= '0;
                else if (clr) prod_r <= '0;
                else if (en)  prod_r <= prod;
            end
            assign prod_acc = prod_r;
        end else begin : g_comb
            assign prod_acc = prod;
        end
    endgenerate

    // explicit sign extension of the product to the accumulator width
    assign prod_ext = {{(AW-2*DW){prod_acc[2*DW-1]}}, prod_acc};

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            a_out <= '0;
            b_out <= '0;
            acc   <= '0;
        end else if (clr) begin
            a_out <= '0;
            b_out <= '0;
            acc   <= '0;
        end else if (en) begin
            a_out <= a_in;
            b_out <= b_in;
            acc   <= acc + prod_ext;
        end
    end

endmodule
