//! Actors have roles; roles allow an action on resources matching a glob.
//! Default deny.

use std::collections::HashMap;
use std::path::Path;

use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
struct Rule {
    action: String,
    #[serde(default)]
    resource: String,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct Policy {
    #[serde(default)]
    roles: HashMap<String, Vec<Rule>>,
    #[serde(default)]
    actors: HashMap<String, Vec<String>>,
}

impl Policy {
    pub fn parse(yaml: &str) -> Result<Self, serde_yaml_ng::Error> {
        serde_yaml_ng::from_str(yaml)
    }

    pub fn load(path: &Path) -> Result<Self, String> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| format!("RBAC policy {}: {e}", path.display()))?;
        Self::parse(&text).map_err(|e| format!("RBAC policy {}: {e}", path.display()))
    }

    #[must_use]
    pub fn check(&self, actor: &str, action: &str, resource: &str) -> bool {
        self.actors.get(actor).is_some_and(|roles| {
            roles.iter().any(|role| {
                self.roles.get(role).is_some_and(|rules| {
                    rules
                        .iter()
                        .any(|rule| rule.action == action && fnmatch(resource, &rule.resource))
                })
            })
        })
    }
}

enum Token {
    Any,
    One,
    Literal(char),
    /// `(negated, ranges)`
    Set(bool, Vec<(char, char)>),
}

/// The members of a bracket expression's body.
fn ranges(body: &[char]) -> Vec<(char, char)> {
    let mut out = Vec::new();
    let mut i = 0;
    while let Some(&low) = body.get(i) {
        match (body.get(i + 1), body.get(i + 2)) {
            (Some('-'), Some(&high)) => {
                // A reversed range matches nothing.
                if low <= high {
                    out.push((low, high));
                }
                i += 3;
            }
            _ => {
                out.push((low, low));
                i += 1;
            }
        }
    }
    out
}

fn tokens(pattern: &str) -> Vec<Token> {
    let chars: Vec<char> = pattern.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while let Some(&c) = chars.get(i) {
        i += 1;
        match c {
            '*' => out.push(Token::Any),
            '?' => out.push(Token::One),
            '[' => {
                // A `]` straight after `[` or `[!` is a member, not the end.
                let mut end = i;
                if chars.get(end) == Some(&'!') {
                    end += 1;
                }
                if chars.get(end) == Some(&']') {
                    end += 1;
                }
                while chars.get(end).is_some_and(|&c| c != ']') {
                    end += 1;
                }
                if end >= chars.len() {
                    out.push(Token::Literal('['));
                    continue;
                }
                let mut body = chars.get(i..end).unwrap_or_default();
                let negated = body.first() == Some(&'!');
                if negated {
                    body = body.get(1..).unwrap_or_default();
                }
                out.push(Token::Set(negated, ranges(body)));
                i = end + 1;
            }
            other => out.push(Token::Literal(other)),
        }
    }
    out
}

fn matches_one(token: &Token, c: char) -> bool {
    match token {
        Token::Any => false,
        Token::One => true,
        Token::Literal(expected) => *expected == c,
        Token::Set(negated, ranges) => {
            ranges.iter().any(|(low, high)| (*low..=*high).contains(&c)) != *negated
        }
    }
}

/// Python's case-sensitive `fnmatch`: `*` crosses `/`, no escaping.
#[must_use]
pub fn fnmatch(name: &str, pattern: &str) -> bool {
    let pattern = tokens(pattern);
    let name: Vec<char> = name.chars().collect();
    let (mut p, mut n) = (0, 0);
    // Where to resume if the text after the last `*` fails to match.
    let mut star: Option<(usize, usize)> = None;
    while n < name.len() {
        match pattern.get(p) {
            Some(Token::Any) => {
                star = Some((p, n));
                p += 1;
            }
            Some(token) if name.get(n).is_some_and(|&c| matches_one(token, c)) => {
                p += 1;
                n += 1;
            }
            _ => match star {
                Some((star_p, star_n)) => {
                    p = star_p + 1;
                    n = star_n + 1;
                    star = Some((star_p, n));
                }
                None => return false,
            },
        }
    }
    pattern
        .get(p..)
        .is_none_or(|rest| rest.iter().all(|t| matches!(t, Token::Any)))
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn denies_by_default() {
        let policy = Policy::parse(
            "roles:\n  approver:\n    - {action: approve, resource: 'playbook:*'}\n\
             actors:\n  alice: [approver]\n",
        )
        .unwrap();
        assert!(policy.check("alice", "approve", "playbook:restart-pod"));
        assert!(!policy.check("alice", "graduate", "playbook:restart-pod"));
        assert!(!policy.check("mallory", "approve", "playbook:restart-pod"));
        assert!(!Policy::default().check("alice", "approve", "x"));
    }
}
