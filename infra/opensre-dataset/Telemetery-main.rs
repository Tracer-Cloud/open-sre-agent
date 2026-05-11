// src/telemetry/ebpf_extraction/main.rs
use aya::programs::KProbe;
use aya::{Bpf, include_bytes_aligned};

fn main() -> Result<(), anyhow::Error> {
    [span_10](start_span)// Load the eBPF bytecode for scientific filtering[span_10](end_span)
    let mut bpf = Bpf::load(include_bytes_aligned!(
        "../../../target/bpfel-unknown-none/debug/tracer-ebpf"
    ))?;

    [span_11](start_span)// Extract telemetry for Hamiltonian physical entropy calculations[span_11](end_span)
    let program: &mut KProbe = bpf.program_mut("trace_system_state").unwrap().try_into()?;
    program.load()?;
    program.attach("sys_enter", 0)?;

    [span_12](start_span)println!("Layer 1: eBPF Extraction Active. Monitoring H-state[span_12](end_span).");
    Ok(())
}
