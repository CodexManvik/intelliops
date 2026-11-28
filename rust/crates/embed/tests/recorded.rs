//! Against sentence-transformers' own vectors. Needs the model files in
//! `rust/models/minilm` (or `INTELLIOPS_MODEL_DIR`); skipped without them.

#![allow(clippy::unwrap_used)]

use std::path::PathBuf;

use intelliops_embed::{Embedder, cosine};
use serde_json::Value;

#[test]
fn vectors_match_sentence_transformers() {
    let rust = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let dir = std::env::var("INTELLIOPS_MODEL_DIR")
        .map_or_else(|_| rust.join("models/minilm"), PathBuf::from);
    if !dir.join("model.safetensors").exists() {
        eprintln!("skipped: no model in {}", dir.display());
        return;
    }
    let embedder = Embedder::load(&dir).unwrap();
    let raw = std::fs::read_to_string(rust.join("app-cases/embeddings.json")).unwrap();
    let recorded: Value = serde_json::from_str(&raw).unwrap();
    let texts = recorded["texts"].as_array().unwrap();
    assert!(texts.len() > 40);
    for (text, theirs) in texts.iter().zip(recorded["vectors"].as_array().unwrap()) {
        let theirs: Vec<f32> = serde_json::from_value(theirs.clone()).unwrap();
        let ours = embedder.encode(text.as_str().unwrap()).unwrap();
        let similarity = cosine(&ours, &theirs).unwrap();
        assert!(similarity > 0.9999, "{similarity} for {text}");
    }
}
