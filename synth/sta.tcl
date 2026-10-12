# OpenSTA script - static timing analysis of the synthesized sa_top netlist.
# Called by run_synth.sh with env vars LIB, NETLIST, CLK_PERIOD (ns).
#
# Three path groups are reported separately:
#   reg2reg : flop -> flop. This is the core's Fmax (multiplier -> adder -> acc).
#   in2reg  : input port -> flop. With a global stall the AXI valid/ready inputs feed
#             `en`, which fans out to every register: a control path, not a datapath.
#   reg2out : flop -> output port.
# I/O delays are 0 (ideal), the clock is ideal, and rst_n (asynchronous, synchronised
# outside the block in a real system) is a false path.

read_liberty $::env(LIB)
read_verilog $::env(NETLIST)
link_design $::env(TOP)

create_clock -name clk -period $::env(CLK_PERIOD) [get_ports clk]
set data_inputs [delete_from_list [all_inputs] [get_ports {clk rst_n}]]
set_input_delay  0 -clock clk $data_inputs
set_output_delay 0 -clock clk [all_outputs]
set_false_path -from [get_ports rst_n]

set regs_clk  [all_registers -clock_pins]
set regs_data [all_registers -data_pins]

proc group_wns {name from to} {
    set paths [find_timing_paths -from $from -to $to -path_delay max -group_count 1]
    if {[llength $paths] == 0} {
        puts "wns_$name none"
        return
    }
    set slack [get_property [lindex $paths 0] slack]
    puts [format "wns_%s %.3f" $name $slack]
}

puts "\n==== Worst reg2reg setup path (core Fmax) ===="
report_checks -path_delay max -from $regs_clk -to $regs_data -digits 3
puts "\n==== Worst in2reg setup path ===="
report_checks -path_delay max -from $data_inputs -to $regs_data -digits 3
puts "\n==== Worst reg2out setup path ===="
report_checks -path_delay max -from $regs_clk -to [all_outputs] -digits 3

puts "\n==== Summary (slack in ns at the given clock period) ===="
puts "clk_period $::env(CLK_PERIOD)"
group_wns reg2reg $regs_clk $regs_data
group_wns in2reg  $data_inputs $regs_data
group_wns reg2out $regs_clk [all_outputs]
report_wns
report_tns
