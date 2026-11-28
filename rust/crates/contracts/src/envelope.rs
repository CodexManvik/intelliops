//! A model travels on the flat bus as JSON in a `data` field, with a stable
//! event `id` that consumers dedupe on.

use serde::{Serialize, de::DeserializeOwned};

#[derive(Debug, thiserror::Error)]
pub enum EnvelopeError {
    #[error("envelope has no data field")]
    MissingData,
    #[error("undecodable payload: {0}")]
    Json(#[from] serde_json::Error),
}

/// The two fields of a bus entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Envelope {
    pub data: String,
    /// Absent on messages published before ids existed.
    pub id: Option<String>,
}

impl Envelope {
    pub fn encode<T: Serialize>(model: &T, id: String) -> Result<Self, EnvelopeError> {
        Ok(Self {
            data: serde_json::to_string(model)?,
            id: Some(id),
        })
    }

    pub fn from_fields<'a>(
        fields: impl IntoIterator<Item = (&'a str, &'a str)>,
    ) -> Result<Self, EnvelopeError> {
        let (mut data, mut id) = (None, None);
        for (key, value) in fields {
            match key {
                "data" => data = Some(value.to_string()),
                "id" => id = Some(value.to_string()),
                _ => {}
            }
        }
        Ok(Self {
            data: data.ok_or(EnvelopeError::MissingData)?,
            id,
        })
    }

    pub fn decode<T: DeserializeOwned>(&self) -> Result<T, EnvelopeError> {
        Ok(serde_json::from_str(&self.data)?)
    }
}
