//! IDE support for resolving the fully qualified name (FQN) of the type at a cursor position.
//!
//! This is a companion to [`goto_type_definition`](crate::goto_type_definition), which returns
//! a *file location*. This module returns the FQN *string* (e.g. `"my_module.MyClass"`) instead,
//! which is useful for display or for tools that need a stable textual type identifier.

use crate::goto::find_goto_target;
use crate::{Db, RangedValue};
use ruff_db::files::{File, FileRange};
use ruff_db::parsed::parsed_module;
use ruff_text_size::{Ranged, TextSize};
use ty_python_semantic::{SemanticModel, type_fqn};

/// Returns the fully qualified name(s) of the *type* at `offset` in `file`.
///
/// Hovering on a name `x` that has type `my_module.MyClass` returns
/// `Some(RangedValue { range: <range of x>, value: vec!["my_module.MyClass"] })`.
///
/// For a union type `Thing | Other` both FQNs are included:
/// `vec!["lib_b.Thing", "lib_c.Other"]`.
///
/// Returns `None` when:
/// - there is no resolvable expression at `offset`, or
/// - every branch of the inferred type has no meaningful FQN.
pub fn type_definition_name(
    db: &dyn Db,
    file: File,
    offset: TextSize,
) -> Option<RangedValue<Vec<String>>> {
    let parsed = parsed_module(db, file).load(db);
    let model = SemanticModel::new(db, file);
    let goto_target = find_goto_target(&model, &parsed, offset)?;

    let ty = goto_target.inferred_type(&model)?;

    tracing::debug!("Resolving FQN for type {} at {:?}", ty.display(db), offset);

    let fqns = type_fqn(db, ty);
    if fqns.is_empty() {
        return None;
    }

    Some(RangedValue {
        range: FileRange::new(file, goto_target.range()),
        value: fqns,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tests::{CursorTest, cursor_test};

    impl CursorTest {
        pub(crate) fn type_definition_name(&self) -> Option<Vec<String>> {
            type_definition_name(&self.db, self.cursor.file, self.cursor.offset)
                .map(|r| r.value.clone())
        }
    }

    #[test]
    fn fqn_of_class_instance() {
        let test = cursor_test(
            r#"
class Foo:
    pass

x: Foo = Foo()
pri<CURSOR>nt(x)
"#,
        );
        // `print` is a builtin function
        let fqns = test.type_definition_name();
        assert!(fqns.is_some());
        let fqns = fqns.unwrap();
        assert_eq!(fqns.len(), 1);
        assert!(
            fqns[0].contains("print"),
            "Expected FQN containing 'print', got: {fqns:?}"
        );
    }

    #[test]
    fn fqn_of_class_literal() {
        let test = cursor_test(
            r#"
class MyClass:
    pass

x = My<CURSOR>Class
"#,
        );
        let fqns = test.type_definition_name();
        assert!(fqns.is_some());
        let fqns = fqns.unwrap();
        assert_eq!(fqns.len(), 1);
        // The class is defined in a temp module; it should end with "MyClass"
        assert!(
            fqns[0].ends_with("MyClass"),
            "Expected FQN ending with 'MyClass', got: {fqns:?}"
        );
    }
}
