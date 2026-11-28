//! An isolation forest layered over the robust detector: outliers need fewer
//! random splits to isolate. Scoring follows scikit-learn's definitions, so the
//! decision boundary means the same thing; the trees themselves differ.

use std::collections::VecDeque;

use serde::{Deserialize, Serialize};

pub const FEATURES: usize = 9;
const TREES: usize = 100;
const SAMPLE: usize = 256;
const CONTAMINATION: f64 = 0.02;
const BUFFER: usize = 4096;
pub const MIN_FIT_SAMPLES: usize = 200;
/// Maps the small anomaly margin onto the z-score scale.
const MODEL_SCALE: f64 = 120.0;
/// Stored with a model, which is refused if the schema differs.
const SCHEMA: &str =
    "value,z_online,hour_sin,hour_cos,day_of_week,kind_metric,kind_log,kind_trace,label_count";

pub type Row = [f64; FEATURES];

/// SplitMix64: a small seeded generator, so a fit is reproducible.
struct Random(u64);

impl Random {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        z ^ (z >> 31)
    }

    fn below(&mut self, n: usize) -> usize {
        (self.next() % n.max(1) as u64) as usize
    }

    /// Uniform in `[0, 1)`.
    fn unit(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
enum Node {
    Leaf {
        size: usize,
    },
    Split {
        feature: usize,
        at: f64,
        left: Box<Node>,
        right: Box<Node>,
    },
}

/// The average path length of an unsuccessful search among `n` points.
fn expected_depth(n: usize) -> f64 {
    match n {
        0 | 1 => 0.0,
        2 => 1.0,
        _ => {
            let n = n as f64;
            2.0 * ((n - 1.0).ln() + std::f64::consts::EULER_GAMMA) - 2.0 * (n - 1.0) / n
        }
    }
}

fn grow(rows: &mut [Row], depth: usize, limit: usize, random: &mut Random) -> Node {
    if depth >= limit || rows.len() <= 1 {
        return Node::Leaf { size: rows.len() };
    }
    // Only features that still vary can split.
    let range = |feature: usize| {
        rows.iter()
            .filter_map(|row| row.get(feature).copied())
            .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| {
                (lo.min(v), hi.max(v))
            })
    };
    let varying: Vec<(usize, f64, f64)> = (0..FEATURES)
        .filter_map(|feature| {
            let (low, high) = range(feature);
            (high > low).then_some((feature, low, high))
        })
        .collect();
    let Some(&(feature, low, high)) = varying.get(random.below(varying.len())) else {
        return Node::Leaf { size: rows.len() };
    };
    let at = low + random.unit() * (high - low);
    let below = |row: &Row| row.get(feature).is_some_and(|v| *v <= at);
    rows.sort_by_key(|row| !below(row));
    let boundary = rows.partition_point(below);
    let (left, right) = rows.split_at_mut(boundary);
    Node::Split {
        feature,
        at,
        left: Box::new(grow(left, depth + 1, limit, random)),
        right: Box::new(grow(right, depth + 1, limit, random)),
    }
}

fn depth_of(mut node: &Node, row: &Row) -> f64 {
    let mut depth = 0.0;
    loop {
        match node {
            Node::Leaf { size } => return depth + expected_depth(*size),
            Node::Split {
                feature,
                at,
                left,
                right,
            } => {
                let goes_left = row.get(*feature).is_some_and(|v| v <= at);
                node = if goes_left { left } else { right };
                depth += 1.0;
            }
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Forest {
    schema: String,
    trees: Vec<Node>,
    sample: usize,
    /// Scores below this are anomalies.
    offset: f64,
}

impl Forest {
    #[must_use]
    pub fn fit(rows: &[Row], seed: u64) -> Option<Self> {
        if rows.len() < 2 {
            return None;
        }
        let mut random = Random(seed);
        let sample = SAMPLE.min(rows.len());
        let limit = (sample as f64).log2().ceil() as usize;
        let mut indices: Vec<usize> = (0..rows.len()).collect();
        let trees = (0..TREES)
            .map(|_| {
                // Sample without replacement: a partial shuffle.
                for i in 0..sample {
                    let j = i + random.below(indices.len() - i);
                    indices.swap(i, j);
                }
                let mut subset: Vec<Row> = indices
                    .iter()
                    .take(sample)
                    .filter_map(|&i| rows.get(i).copied())
                    .collect();
                grow(&mut subset, 0, limit, &mut random)
            })
            .collect();
        let mut forest = Self {
            schema: SCHEMA.to_string(),
            trees,
            sample,
            offset: 0.0,
        };
        let mut scores: Vec<f64> = rows.iter().map(|row| forest.score(row)).collect();
        scores.sort_unstable_by(f64::total_cmp);
        // The contamination percentile, linearly interpolated.
        let position = CONTAMINATION * (scores.len() - 1) as f64;
        let lower = *scores.get(position.floor() as usize)?;
        let upper = *scores.get(position.ceil() as usize)?;
        forest.offset = lower + (upper - lower) * position.fract();
        Some(forest)
    }

    /// In `[-1, 0]`; lower is more anomalous.
    fn score(&self, row: &Row) -> f64 {
        let mean = self.trees.iter().map(|t| depth_of(t, row)).sum::<f64>()
            / self.trees.len().max(1) as f64;
        -(2f64.powf(-mean / expected_depth(self.sample).max(f64::MIN_POSITIVE)))
    }

    /// Zero for a point the model calls normal, growing past the boundary.
    #[must_use]
    pub fn anomaly(&self, row: &Row) -> f64 {
        (self.offset - self.score(row)).max(0.0) * MODEL_SCALE
    }
}

/// Recent feature rows and the model fitted from them, if any.
#[derive(Debug, Clone, Default)]
pub struct Model {
    rows: VecDeque<Row>,
    forest: Option<Forest>,
}

/// What a model sees of one event.
#[derive(Debug, Clone, Copy)]
pub struct Sighting {
    pub value: Option<f64>,
    pub online_score: f64,
    pub hour: u32,
    /// Monday is 0.
    pub weekday: u32,
    /// `[metric, log, trace]`
    pub kind: usize,
    pub labels: usize,
}

impl Model {
    /// Remember the event and return the higher of the online and model scores.
    pub fn observe(&mut self, sighting: Sighting) -> f64 {
        let angle = std::f64::consts::TAU * f64::from(sighting.hour) / 24.0;
        let kind = |index: usize| f64::from(u8::from(sighting.kind == index));
        let row: Row = [
            sighting.value.unwrap_or(0.0),
            sighting.online_score,
            angle.sin(),
            angle.cos(),
            f64::from(sighting.weekday),
            kind(0),
            kind(1),
            kind(2),
            sighting.labels as f64,
        ];
        if self.rows.len() == BUFFER {
            self.rows.pop_front();
        }
        self.rows.push_back(row);
        self.forest
            .as_ref()
            .map_or(sighting.online_score, |forest| {
                sighting.online_score.max(forest.anomaly(&row))
            })
    }

    /// Fit from the buffered rows. `false` if there are too few.
    pub fn fit(&mut self) -> bool {
        if self.rows.len() < MIN_FIT_SAMPLES {
            return false;
        }
        let rows: Vec<Row> = self.rows.iter().copied().collect();
        match Forest::fit(&rows, 0) {
            Some(forest) => {
                self.forest = Some(forest);
                true
            }
            None => false,
        }
    }

    #[must_use]
    pub fn serialize(&self) -> Option<Vec<u8>> {
        serde_json::to_vec(self.forest.as_ref()?).ok()
    }

    /// `false`, staying as it was, for anything unreadable or of another schema
    /// (a model saved by the Python stack is one).
    pub fn load(&mut self, blob: &[u8]) -> bool {
        match serde_json::from_slice::<Forest>(blob) {
            Ok(forest) if forest.schema == SCHEMA => {
                self.forest = Some(forest);
                true
            }
            _ => false,
        }
    }

    pub fn reset(&mut self) {
        self.rows.clear();
        self.forest = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_outlier_scores_higher_than_the_crowd() {
        let mut random = Random(7);
        let rows: Vec<Row> = (0..500)
            .map(|_| {
                let mut row = [0.0; FEATURES];
                row[0] = 50.0 + random.unit() * 4.0;
                row[1] = random.unit();
                row
            })
            .collect();
        let forest = Forest::fit(&rows, 0).map(|f| {
            let mut outlier = [0.0; FEATURES];
            outlier[0] = 500.0;
            outlier[1] = 30.0;
            (f.anomaly(&outlier), f.anomaly(&rows[0]))
        });
        let (outlier, normal) = forest.unwrap_or_default();
        assert!(outlier > normal + 3.0, "{outlier} vs {normal}");
    }

    #[test]
    fn a_model_round_trips_and_rejects_other_blobs() {
        let mut model = Model::default();
        for i in 0..MIN_FIT_SAMPLES {
            model.observe(Sighting {
                value: Some(i as f64),
                online_score: 0.0,
                hour: 1,
                weekday: 1,
                kind: 0,
                labels: 0,
            });
        }
        assert!(model.fit());
        let blob = model.serialize().unwrap_or_default();
        assert!(Model::default().load(&blob));
        assert!(!Model::default().load(b"\x80\x04joblib"));
    }
}
