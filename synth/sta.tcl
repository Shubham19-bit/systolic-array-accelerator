# OpenSTA script - static timing analysis of the synthesized sa_top netlist.
# Called by run_synth.sh with env vars LIB, NETLIST, CLK_PERIOD (ns).

read_liberty $::env(LIB)
read_verilog $::env(NETLIST)
link_design sa_top

create_clock -name clk -period $::env(CLK_PERIOD) [get_ports clk]
set_input_delay  0 -clock clk [delete_from_list [all_inputs] [get_ports clk]]
set_output_delay 0 -clock clk [all_outputs]

puts "\n==== Worst setup path ===="
report_checks -path_delay max -digits 3
puts "\n==== Worst hold path ===="
report_checks -path_delay min -digits 3
puts "\n==== Summary ===="
report_wns
report_tns
