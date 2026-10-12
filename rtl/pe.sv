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
// en  : global stall/advance. When 0 every register holds its value.
// clr : synchronous clear of accumulator and forwarding registers.
// -----------------------------------------------------------------------------
module pe #(
    parameter int DW = 8,   // operand width (signed INT8 by default)
    parameter int AW = 32   // accumulator width
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

    logic signed [2*DW-1:0] prod;
    logic signed [AW-1:0]   prod_ext;

    assign prod     = a_in * b_in;
    // explicit sign extension of the product to the accumulator width
    assign prod_ext = {{(AW-2*DW){prod[2*DW-1]}}, prod};

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
