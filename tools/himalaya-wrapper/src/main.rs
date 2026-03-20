use std::os::unix::process::CommandExt;
use std::process::Command;

const HIMALAYA_BIN: &str = "/usr/local/bin/himalaya";
const HIMALAYA_HOME: &str = "/home/himalaya";

// Global himalaya flags that consume the next argument as their value.
// Must be kept in sync with himalaya's CLI. Unknown flags are assumed to be
// boolean and do NOT consume the next argument.
const FLAGS_WITH_VALUES: &[&str] = &[
    "-a", "--account",
    "-f", "--folder",
    "-o", "--output",
    "-c", "--config",
    "-C", "--color",
    "-l", "--log-level",
];

/// Allowed (command, subcommand) pairs.
/// Everything not in this list is blocked.
const ALLOWED: &[(&str, &str)] = &[
    ("account", "list"),
    ("attachment", "download"),
    ("envelope", "list"),
    ("flag", "add"),
    ("flag", "remove"),
    ("folder", "list"),
    ("message", "export"),
    ("message", "read"),
    ("message", "save"),
    ("template", "save"),
];

/// Safe IMAP flag values for `flag add` / `flag remove`.
/// The `\Deleted` flag is intentionally excluded — it marks messages for
/// permanent removal on EXPUNGE.
const SAFE_FLAGS: &[&str] = &[
    "seen", "answered", "flagged", "draft",
];

/// Extract the first two positional (non-flag) args from a himalaya argv.
/// Skips global flags and their values. Returns None if fewer than two positionals found.
fn find_command_subcommand<'a>(args: &[&'a str]) -> Option<(&'a str, &'a str)> {
    let mut positionals: Vec<&str> = Vec::new();
    let mut skip_next = false;

    for &arg in args {
        if skip_next {
            skip_next = false;
            continue;
        }
        if arg == "--" {
            break;
        }
        if arg.starts_with('-') {
            // Inline value like --output=json
            if arg.contains('=') {
                continue;
            }
            // Known flag that consumes the next arg as its value
            if FLAGS_WITH_VALUES.contains(&arg) {
                skip_next = true;
                continue;
            }
            // Boolean flag, skip it
            continue;
        }
        positionals.push(arg);
        if positionals.len() == 2 {
            break;
        }
    }

    if positionals.len() >= 2 {
        Some((positionals[0], positionals[1]))
    } else {
        None
    }
}

/// Collect all positional args (skipping global flags and their values).
fn collect_positionals<'a>(args: &[&'a str]) -> Vec<&'a str> {
    let mut positionals: Vec<&str> = Vec::new();
    let mut skip_next = false;

    for &arg in args {
        if skip_next {
            skip_next = false;
            continue;
        }
        if arg == "--" {
            break;
        }
        if arg.starts_with('-') {
            if arg.contains('=') {
                continue;
            }
            if FLAGS_WITH_VALUES.contains(&arg) {
                skip_next = true;
                continue;
            }
            continue;
        }
        positionals.push(arg);
    }
    positionals
}

/// For `flag add` / `flag remove`, verify all flag values are in SAFE_FLAGS.
/// Positionals layout: ["flag", "add"|"remove", <id>, <flag1>, <flag2>, ...]
fn flag_values_safe(positionals: &[&str]) -> bool {
    // Need at least: command, subcommand, message-id, one flag
    if positionals.len() < 4 {
        return false;
    }
    for &flag_val in &positionals[3..] {
        if !SAFE_FLAGS.contains(&flag_val.to_ascii_lowercase().as_str()) {
            return false;
        }
    }
    true
}

fn is_allowed(args: &[&str]) -> bool {
    match find_command_subcommand(args) {
        Some((cmd, subcmd)) => {
            if !ALLOWED.contains(&(cmd, subcmd)) {
                return false;
            }
            if cmd == "flag" {
                return flag_values_safe(&collect_positionals(args));
            }
            true
        }
        None => false,
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let arg_refs: Vec<&str> = args.iter().map(String::as_str).collect();

    if !is_allowed(&arg_refs) {
        eprintln!("himalaya-wrapper: operation not permitted");
        std::process::exit(1);
    }

    // exec() replaces this process with himalaya.
    // HOME is set to himalaya-svc's home so himalaya finds its config.
    // Environment is cleared to prevent injection via env vars.
    let err = Command::new(HIMALAYA_BIN)
        .args(&args)
        .env_clear()
        .env("HOME", HIMALAYA_HOME)
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .exec();

    // exec() only returns on error
    eprintln!("himalaya-wrapper: failed to exec himalaya: {err}");
    std::process::exit(1);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fcs<'a>(args: &[&'a str]) -> Option<(&'a str, &'a str)> {
        find_command_subcommand(args)
    }

    // --- arg parser tests ---

    #[test]
    fn test_simple_command_subcommand() {
        assert_eq!(fcs(&["envelope", "list"]), Some(("envelope", "list")));
    }

    #[test]
    fn test_with_short_flag_value() {
        assert_eq!(
            fcs(&["-a", "personal", "envelope", "list"]),
            Some(("envelope", "list"))
        );
    }

    #[test]
    fn test_with_long_flag_value() {
        assert_eq!(
            fcs(&["--account", "personal", "envelope", "list"]),
            Some(("envelope", "list"))
        );
    }

    #[test]
    fn test_with_inline_flag_value() {
        assert_eq!(
            fcs(&["--output=json", "envelope", "list"]),
            Some(("envelope", "list"))
        );
    }

    #[test]
    fn test_with_boolean_flag() {
        assert_eq!(
            fcs(&["--debug", "envelope", "list"]),
            Some(("envelope", "list"))
        );
    }

    #[test]
    fn test_multiple_flags_before_command() {
        assert_eq!(
            fcs(&["-a", "work", "-o", "json", "message", "read"]),
            Some(("message", "read"))
        );
    }

    #[test]
    fn test_empty_args() {
        assert_eq!(fcs(&[]), None);
    }

    #[test]
    fn test_only_command_no_subcommand() {
        assert_eq!(fcs(&["envelope"]), None);
    }

    #[test]
    fn test_stops_before_double_dash() {
        assert_eq!(fcs(&["--", "envelope", "list"]), None);
    }

    // --- allowlist tests ---

    #[test]
    fn test_allowed_envelope_list() {
        assert!(is_allowed(&["envelope", "list"]));
    }

    #[test]
    fn test_allowed_with_flags() {
        assert!(is_allowed(&["-o", "json", "envelope", "list"]));
    }

    #[test]
    fn test_allowed_message_read() {
        assert!(is_allowed(&["message", "read", "42"]));
    }

    #[test]
    fn test_allowed_message_save() {
        assert!(is_allowed(&["--folder", "Drafts", "message", "save"]));
    }

    #[test]
    fn test_allowed_flag_add_seen() {
        assert!(is_allowed(&["flag", "add", "42", "seen"]));
    }

    #[test]
    fn test_allowed_flag_add_flagged() {
        assert!(is_allowed(&["flag", "add", "42", "flagged"]));
    }

    #[test]
    fn test_allowed_flag_add_answered() {
        assert!(is_allowed(&["flag", "add", "42", "answered"]));
    }

    #[test]
    fn test_allowed_flag_add_draft() {
        assert!(is_allowed(&["flag", "add", "42", "draft"]));
    }

    #[test]
    fn test_allowed_flag_add_multiple_safe() {
        assert!(is_allowed(&["flag", "add", "42", "seen", "flagged"]));
    }

    #[test]
    fn test_allowed_flag_add_case_insensitive() {
        assert!(is_allowed(&["flag", "add", "42", "Seen"]));
    }

    #[test]
    fn test_blocked_flag_add_deleted() {
        assert!(!is_allowed(&["flag", "add", "42", "deleted"]));
    }

    #[test]
    fn test_blocked_flag_add_deleted_case_insensitive() {
        assert!(!is_allowed(&["flag", "add", "42", "Deleted"]));
    }

    #[test]
    fn test_blocked_flag_add_deleted_mixed_with_safe() {
        assert!(!is_allowed(&["flag", "add", "42", "seen", "deleted"]));
    }

    #[test]
    fn test_blocked_flag_add_unknown_flag() {
        assert!(!is_allowed(&["flag", "add", "42", "custom-flag"]));
    }

    #[test]
    fn test_blocked_flag_add_no_flag_value() {
        assert!(!is_allowed(&["flag", "add", "42"]));
    }

    #[test]
    fn test_blocked_flag_remove_deleted() {
        // Even removing deleted is blocked — no reason to interact with it
        assert!(!is_allowed(&["flag", "remove", "42", "deleted"]));
    }

    #[test]
    fn test_allowed_flag_remove_seen() {
        assert!(is_allowed(&["flag", "remove", "42", "seen"]));
    }

    #[test]
    fn test_allowed_flag_remove_flagged() {
        assert!(is_allowed(&["flag", "remove", "42", "flagged"]));
    }

    #[test]
    fn test_allowed_folder_list() {
        assert!(is_allowed(&["folder", "list"]));
    }

    #[test]
    fn test_allowed_attachment_download() {
        assert!(is_allowed(&["attachment", "download", "42"]));
    }

    #[test]
    fn test_allowed_template_save() {
        assert!(is_allowed(&["template", "save"]));
    }

    #[test]
    fn test_allowed_template_save_with_folder() {
        assert!(is_allowed(&["--folder", "Drafts", "template", "save"]));
    }

    #[test]
    fn test_blocked_message_send() {
        assert!(!is_allowed(&["message", "send"]));
    }

    #[test]
    fn test_blocked_message_write() {
        assert!(!is_allowed(&["message", "write"]));
    }

    #[test]
    fn test_blocked_message_reply() {
        assert!(!is_allowed(&["message", "reply", "42"]));
    }

    #[test]
    fn test_blocked_message_forward() {
        assert!(!is_allowed(&["message", "forward", "42"]));
    }

    #[test]
    fn test_blocked_flag_set() {
        assert!(!is_allowed(&["flag", "set", "42", "seen"]));
    }

    #[test]
    fn test_blocked_template_send() {
        assert!(!is_allowed(&["template", "send"]));
    }

    #[test]
    fn test_blocked_template_write() {
        assert!(!is_allowed(&["template", "write"]));
    }

    #[test]
    fn test_blocked_template_reply() {
        assert!(!is_allowed(&["template", "reply"]));
    }

    #[test]
    fn test_blocked_template_forward() {
        assert!(!is_allowed(&["template", "forward"]));
    }

    #[test]
    fn test_blocked_empty() {
        assert!(!is_allowed(&[]));
    }

    #[test]
    fn test_blocked_unknown_command() {
        assert!(!is_allowed(&["contact", "list"]));
    }
}
