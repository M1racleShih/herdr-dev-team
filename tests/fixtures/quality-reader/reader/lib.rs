use std::convert::TryInto;

pub fn read_amount(bytes: &[u8]) -> Result<i32, &'static str> {
    match bytes.split_first() {
        Some((b'I', value)) => value
            .try_into()
            .map(i32::from_le_bytes)
            .map_err(|_| "invalid v1 integer"),
        Some((b'D', value)) => read_decimal(value),
        Some((b'C', value)) => std::str::from_utf8(value)
            .ok()
            .and_then(|value| value.trim_end().parse::<i32>().ok())
            .ok_or("invalid v2 amount"),
        _ => Err("unknown format"),
    }
}

fn read_decimal(value: &[u8]) -> Result<i32, &'static str> {
    let text = std::str::from_utf8(value).map_err(|_| "invalid v1 decimal")?;
    let (whole, fraction) = text.split_once('.').ok_or("invalid v1 decimal")?;
    if whole.is_empty()
        || fraction.len() != 2
        || !whole.bytes().chain(fraction.bytes()).all(|byte| byte.is_ascii_digit())
    {
        return Err("invalid v1 decimal");
    }
    let whole: i32 = whole.parse().map_err(|_| "invalid v1 decimal")?;
    let fraction: i32 = fraction.parse().map_err(|_| "invalid v1 decimal")?;
    whole.checked_mul(100)
        .and_then(|cents| cents.checked_add(fraction))
        .ok_or("v1 decimal overflows")
}

#[test]
fn decimal_fraction_is_preserved() {
    assert_eq!(read_amount(b"D12.34"), Ok(1234), "decimal fraction is preserved");
}
