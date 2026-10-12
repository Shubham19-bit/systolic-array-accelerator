// -----------------------------------------------------------------------------
// sram_sp.sv - Simple dual-port memory: 1 write port, 1 read port, synchronous read.
//
// The read data register is updated on the clock edge after `re`, so a read issued in
// cycle t returns its data in cycle t+1 (like an SRAM macro with a registered output).
// There is no reset: the contents are undefined until written. Reading and writing the
// same address in one cycle returns the old data (the user never does).
// Behavioural model; a real design would replace it by a memory macro of the same shape.
// -----------------------------------------------------------------------------
module sram_sp #(
    parameter int DEPTH = 64,
    parameter int WIDTH = 32,
    localparam int AWD  = (DEPTH > 1) ? $clog2(DEPTH) : 1
) (
    input  logic             clk,
    input  logic             we,
    input  logic [AWD-1:0]   waddr,
    input  logic [WIDTH-1:0] wdata,
    input  logic             re,
    input  logic [AWD-1:0]   raddr,
    output logic [WIDTH-1:0] rdata
);

    logic [WIDTH-1:0] mem [0:DEPTH-1];

    always_ff @(posedge clk) begin
        if (we) mem[waddr] <= wdata;
        if (re) rdata <= mem[raddr];
    end

endmodule
