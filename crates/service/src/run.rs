//! Process lifecycle: logging, serving, shutdown and the container healthcheck.

use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::time::Duration;

use axum::Router;
use tracing_subscriber::EnvFilter;

use crate::Config;

/// `LOG_FORMAT=text|json`, `LOG_LEVEL=INFO` (Python level names).
pub fn init_logging(config: &Config) {
    let level = match config
        .get("log_level", "INFO")
        .to_ascii_uppercase()
        .as_str()
    {
        "DEBUG" => "debug",
        "WARNING" => "warn",
        "ERROR" | "CRITICAL" => "error",
        _ => "info",
    };
    let builder = tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::new(level))
        .with_writer(std::io::stderr);
    // A second call (tests) keeps the first subscriber.
    let _ = if config.get("log_format", "text") == "json" {
        builder.json().flatten_event(true).try_init()
    } else {
        builder.try_init()
    };
}

/// Resolves on Ctrl-C or SIGTERM, so containers stop promptly.
pub async fn shutdown_signal() {
    let interrupt = async {
        if tokio::signal::ctrl_c().await.is_err() {
            std::future::pending::<()>().await;
        }
    };
    #[cfg(unix)]
    let terminate = async {
        use tokio::signal::unix::{SignalKind, signal};
        match signal(SignalKind::terminate()) {
            Ok(mut stream) => {
                stream.recv().await;
            }
            Err(_) => std::future::pending().await,
        }
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();
    tokio::select! {
        () = interrupt => {}
        () = terminate => {}
    }
}

pub async fn serve(app: Router, port: u16) -> std::io::Result<()> {
    let address = SocketAddr::from((Ipv4Addr::UNSPECIFIED, port));
    let listener = tokio::net::TcpListener::bind(address).await?;
    tracing::info!(%address, "listening");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
}

/// `GET /ready` on this container, so images need no shell or curl.
pub fn healthcheck(port: u16) -> std::io::Result<()> {
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let timeout = Duration::from_secs(2);
    let mut stream = TcpStream::connect_timeout(&address, timeout)?;
    stream.set_read_timeout(Some(timeout))?;
    stream.write_all(b"GET /ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")?;
    let mut status = [0u8; 12];
    stream.read_exact(&mut status)?;
    if status.ends_with(b"200") {
        Ok(())
    } else {
        Err(std::io::Error::other("not ready"))
    }
}
