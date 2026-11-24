#[path = "lib.rs"]
mod reader;

fn main() -> Result<(), String> {
    let path = std::env::args().nth(1).ok_or("provide a reader file")?;
    let bytes = std::fs::read(path).map_err(|error| error.to_string())?;
    let amount = reader::read_amount(&bytes)?;
    println!("{}", amount);
    Ok(())
}
