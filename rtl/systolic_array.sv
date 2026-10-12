// -----------------------------------------------------------------------------
// systolic_array.sv - N x N grid of output-stationary PEs.
//
// Operand A enters on the left edge (one lane per row) and flows right.
// Operand B enters on the top edge (one lane per column) and flows down.
// PE(i,j) accumulates C[i][j] = sum_k A[i][k] * B[k][j].
//
// Inputs must already be skewed (see skew_buffer.sv).
// acc_flat packs C row-major: C[i][j] at acc_flat[(i*N + j)*AW +: AW].
//
// Internal wiring uses flat packed vectors (not 2-D unpacked arrays) so the
// design is portable across Icarus Verilog, Verilator and Yosys.
// -----------------------------------------------------------------------------
module systolic_array #(
    parameter int N    = 4,
    parameter int DW   = 8,
    parameter int AW   = 32,
    parameter int PIPE = 1       // see pe.sv
) (
    input  logic              clk,
    input  logic              rst_n,
    input  logic              en,
    input  logic              clr,
    input  logic [N*DW-1:0]   a_left,   // row i operand at a_left[i*DW +: DW]
    input  logic [N*DW-1:0]   b_top,    // col j operand at b_top[j*DW +: DW]
    output logic [N*N*AW-1:0] acc_flat
);

    // a_h: A operand entering PE(i,j) at index (i*(N+1) + j). Column N is the
    //      unused output of the last PE in each row.
    // b_v: B operand entering PE(i,j) at index (i*N + j). Row N is the unused
    //      output of the last PE in each column.
    // The edge slots (a_h column N, b_v row N) are the last PE's outputs and are
    // intentionally unconnected, so lint is told not to flag them.
    /* verilator lint_off UNUSEDSIGNAL */
    wire [N*(N+1)*DW-1:0] a_h;
    wire [(N+1)*N*DW-1:0] b_v;
    /* verilator lint_on UNUSEDSIGNAL */

    genvar i, j;
    generate
        for (i = 0; i < N; i = i + 1) begin : g_left
            assign a_h[(i*(N+1))*DW +: DW] = a_left[i*DW +: DW];
        end
        for (j = 0; j < N; j = j + 1) begin : g_top
            assign b_v[j*DW +: DW] = b_top[j*DW +: DW];
        end

        for (i = 0; i < N; i = i + 1) begin : g_row
            for (j = 0; j < N; j = j + 1) begin : g_col
                pe #(.DW(DW), .AW(AW), .PIPE(PIPE)) u_pe (
                    .clk   (clk),
                    .rst_n (rst_n),
                    .en    (en),
                    .clr   (clr),
                    .a_in  (a_h[(i*(N+1) + j    )*DW +: DW]),
                    .b_in  (b_v[(i*N     + j    )*DW +: DW]),
                    .a_out (a_h[(i*(N+1) + j + 1)*DW +: DW]),
                    .b_out (b_v[((i+1)*N + j    )*DW +: DW]),
                    .acc   (acc_flat[(i*N + j)*AW +: AW])
                );
            end
        end
    endgenerate

endmodule
