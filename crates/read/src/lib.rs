//! The read side: tail the event stream, serve the console.

pub mod peers;
mod projection;
pub mod routes;

pub use projection::{Card, OutcomeRow, ReadModel};
