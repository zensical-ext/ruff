//! Handler for the custom `ty/typeDefinitionName` LSP request.
//!
//! This request returns the *fully qualified name* (FQN) of the type at a given cursor
//! position, rather than a file location.  Clients that prefer a stable string identifier
//! over jumping to a source file can use this endpoint.
//!
//! # Protocol
//!
//! - **Method**: `ty/typeDefinitionName`
//! - **Params**: [`lsp_types::TextDocumentPositionParams`]
//! - **Result**: `Option<TypeDefinitionNameResponse>`
//!
//! # Example response
//! ```json
//! { "name": "my_module.MyClass", "range": { "start": {...}, "end": {...} } }
//! ```

use std::borrow::Cow;

use lsp_types::{TextDocumentPositionParams, Url};
use serde::{Deserialize, Serialize};
use ty_ide::type_definition_name;
use ty_project::ProjectDatabase;

use crate::document::{FileRangeExt, PositionExt};
use crate::server::api::traits::{
    BackgroundDocumentRequestHandler, RequestHandler, RetriableRequestHandler,
};
use crate::session::DocumentSnapshot;
use crate::session::client::Client;

// ──────────────────────────────────────────────────────────────────────────────
// LSP request type

/// Marker type that satisfies [`lsp_types::request::Request`] for our custom method.
pub(crate) enum TypeDefinitionNameRequest {}

impl lsp_types::request::Request for TypeDefinitionNameRequest {
    /// The standard position params are re-used so every LSP client that already
    /// knows how to send a hover/goto request can trivially call this endpoint.
    type Params = TextDocumentPositionParams;
    type Result = Option<TypeDefinitionNameResponse>;
    const METHOD: &'static str = "ty/typeDefinitionName";
}

// ──────────────────────────────────────────────────────────────────────────────
// Response payload

/// Response payload for the `ty/typeDefinitionName` request.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct TypeDefinitionNameResponse {
    /// Fully qualified names of the type, in declaration order.
    ///
    /// Contains exactly one entry for concrete types (e.g. `["my_module.MyClass"]`).
    /// Contains one entry per branch for union types
    /// (e.g. `["lib_b.Thing", "lib_c.Other"]` for `Thing | Other`).
    pub names: Vec<String>,

    /// The source range of the token/expression that was resolved.
    ///
    /// Clients can use this to highlight the token or anchor a tooltip.
    pub range: lsp_types::Range,
}

// ──────────────────────────────────────────────────────────────────────────────
// Request handler

pub(crate) struct TypeDefinitionNameRequestHandler;

impl RequestHandler for TypeDefinitionNameRequestHandler {
    type RequestType = TypeDefinitionNameRequest;
}

impl BackgroundDocumentRequestHandler for TypeDefinitionNameRequestHandler {
    fn document_url(params: &TextDocumentPositionParams) -> Cow<'_, Url> {
        Cow::Borrowed(&params.text_document.uri)
    }

    fn run_with_snapshot(
        db: &ProjectDatabase,
        snapshot: &DocumentSnapshot,
        _client: &Client,
        params: TextDocumentPositionParams,
    ) -> crate::server::Result<Option<TypeDefinitionNameResponse>> {
        if snapshot
            .workspace_settings()
            .is_language_services_disabled()
        {
            return Ok(None);
        }

        let Some(file) = snapshot.to_notebook_or_file(db) else {
            return Ok(None);
        };

        let Some(offset) =
            params
                .position
                .to_text_size(db, file, snapshot.url(), snapshot.encoding())
        else {
            return Ok(None);
        };

        let Some(ranged) = type_definition_name(db, file, offset) else {
            return Ok(None);
        };

        let Some(lsp_range) = ranged.range.to_lsp_range(db, snapshot.encoding()) else {
            return Ok(None);
        };

        Ok(Some(TypeDefinitionNameResponse {
            names: ranged.value,
            range: lsp_range.local_range(),
        }))
    }
}

impl RetriableRequestHandler for TypeDefinitionNameRequestHandler {}
