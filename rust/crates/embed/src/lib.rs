//! The same model sentence-transformers ran: BERT, mean pooled, unit length.

use std::path::Path;

use candle_core::{DType, Device, Tensor};
use candle_nn::VarBuilder;
use candle_transformers::models::bert::{BertModel, Config as BertConfig};
use tokenizers::{Tokenizer, TruncationParams};

const MAX_TOKENS: usize = 256;

#[derive(Debug, thiserror::Error)]
pub enum EmbedError {
    #[error("model file {0}: {1}")]
    File(String, std::io::Error),
    #[error("model config: {0}")]
    Config(#[from] serde_json::Error),
    #[error("model: {0}")]
    Model(#[from] candle_core::Error),
    #[error("tokenizer: {0}")]
    Tokenizer(String),
}

pub struct Embedder {
    model: BertModel,
    tokenizer: Tokenizer,
}

fn read(dir: &Path, name: &str) -> Result<Vec<u8>, EmbedError> {
    let path = dir.join(name);
    std::fs::read(&path).map_err(|e| EmbedError::File(path.display().to_string(), e))
}

impl Embedder {
    /// Load `config.json`, `tokenizer.json` and `model.safetensors` from `dir`.
    pub fn load(dir: &Path) -> Result<Self, EmbedError> {
        let device = Device::Cpu;
        let config: BertConfig = serde_json::from_slice(&read(dir, "config.json")?)?;
        let tensors =
            candle_core::safetensors::load_buffer(&read(dir, "model.safetensors")?, &device)?;
        let weights = VarBuilder::from_tensors(tensors, DType::F32, &device);
        let model = BertModel::load(weights, &config)?;
        let mut tokenizer = Tokenizer::from_bytes(read(dir, "tokenizer.json")?)
            .map_err(|e| EmbedError::Tokenizer(e.to_string()))?;
        tokenizer
            .with_padding(None)
            .with_truncation(Some(TruncationParams {
                max_length: MAX_TOKENS,
                ..TruncationParams::default()
            }))
            .map_err(|e| EmbedError::Tokenizer(e.to_string()))?;
        Ok(Self { model, tokenizer })
    }

    /// A unit-length vector. CPU bound: call off the async runtime.
    pub fn encode(&self, text: &str) -> Result<Vec<f32>, EmbedError> {
        let encoding = self
            .tokenizer
            .encode(text, true)
            .map_err(|e| EmbedError::Tokenizer(e.to_string()))?;
        let ids = Tensor::new(encoding.get_ids(), &Device::Cpu)?.unsqueeze(0)?;
        let types = ids.zeros_like()?;
        let hidden = self.model.forward(&ids, &types, None)?;
        let mut vector = hidden.mean(1)?.squeeze(0)?.to_vec1::<f32>()?;
        let norm = vector.iter().map(|v| v * v).sum::<f32>().sqrt();
        if norm > 0.0 {
            for value in &mut vector {
                *value /= norm;
            }
        }
        Ok(vector)
    }
}

/// `None` for a zero or mismatched vector.
#[must_use]
pub fn cosine(a: &[f32], b: &[f32]) -> Option<f64> {
    if a.len() != b.len() {
        return None;
    }
    let dot: f64 = a
        .iter()
        .zip(b)
        .map(|(x, y)| f64::from(*x) * f64::from(*y))
        .sum();
    let norm = |v: &[f32]| v.iter().map(|x| f64::from(*x).powi(2)).sum::<f64>().sqrt();
    let scale = norm(a) * norm(b);
    (scale != 0.0).then(|| dot / scale)
}
