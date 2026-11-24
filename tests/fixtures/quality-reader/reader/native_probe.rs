#[path = "lib.rs"]
mod reader;

fn main() -> Result<(), String> {
    for name in std::env::args().skip(1) {
        let bytes = std::fs::read(&name).map_err(|error| error.to_string())?;
        let amount = reader::read_amount(&bytes)?;
        println!("{name}\t{amount}");
    }
    Ok(())
}
