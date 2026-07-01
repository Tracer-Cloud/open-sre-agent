#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || {
  printf '%s\n' "Error: install.sh requires bash. Run 'bash install.sh' or pipe it into bash." >&2
  exit 1
}

set -euo pipefail

if [ -t 1 ]; then
  COLOR_RESET=$'\033[0m'
  COLOR_BOLD=$'\033[1m'
  COLOR_DIM=$'\033[2m'
  COLOR_RED=$'\033[31m'
  COLOR_GREEN=$'\033[32m'
  COLOR_YELLOW=$'\033[33m'
  COLOR_CYAN=$'\033[36m'
  SUCCESS_MARK="✓"
else
  COLOR_RESET=""
  COLOR_BOLD=""
  COLOR_DIM=""
  COLOR_RED=""
  COLOR_GREEN=""
  COLOR_YELLOW=""
  COLOR_CYAN=""
  SUCCESS_MARK="Success:"
fi

REPO="${OPENSRE_INSTALL_REPO:-Tracer-Cloud/opensre}"
DEFAULT_INSTALL_DIR="${HOME}/.local/bin"
USER_INSTALL_DIR_CANDIDATES="${OPENSRE_USER_INSTALL_DIR_CANDIDATES:-$HOME/.local/bin:$HOME/bin}"
SYSTEM_INSTALL_DIR_CANDIDATES="${OPENSRE_SYSTEM_INSTALL_DIR_CANDIDATES:-/opt/homebrew/bin:/usr/local/bin:/opt/local/bin}"
INSTALL_DIR="${OPENSRE_INSTALL_DIR:-}"
INSTALL_DIR_OVERRIDE=0
INSTALL_CHANNEL="${OPENSRE_INSTALL_CHANNEL:-main}"
INSTALL_CHANNEL_EXPLICIT=0
[ -n "${OPENSRE_INSTALL_CHANNEL:-}" ] && INSTALL_CHANNEL_EXPLICIT=1
MAIN_RELEASE_TAG="${OPENSRE_MAIN_RELEASE_TAG:-main-build}"
BIN_NAME="opensre"
PROGRESS_PID=""
requested_version="${OPENSRE_VERSION:-}"

[ -n "$INSTALL_DIR" ] && INSTALL_DIR_OVERRIDE=1
requested_version="${requested_version#v}"

log() {
  printf '%s\n' "$*"
}

warn() {
  printf '%sWarning:%s %s\n' "${COLOR_YELLOW:-}" "${COLOR_RESET:-}" "$*" >&2
}

die() {
  printf '%sError:%s %s\n' "${COLOR_RED:-}" "${COLOR_RESET:-}" "$*" >&2
  exit 1
}

success() {
  printf '%s%s %s%s\n' "${COLOR_GREEN:-}" "${SUCCESS_MARK:-Success:}" "$*" "${COLOR_RESET:-}"
}

step() {
  printf '%s%s%s\n' "${COLOR_CYAN:-}" "$*" "${COLOR_RESET:-}"
}

install_verbose() {
  case "${OPENSRE_INSTALL_VERBOSE:-}" in
    1|true|TRUE|yes|YES)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_interactive_terminal() {
  [ -t 1 ] && [ "${TERM:-}" != "dumb" ] && ! install_verbose
}

terminal_supports_unicode() {
  local locale_value="${LC_ALL:-${LC_CTYPE:-${LANG:-}}}"

  case "$locale_value" in
    *UTF-8*|*utf-8*|*UTF8*|*utf8*)
      return 0
      ;;
  esac

  case "${TERM_PROGRAM:-}" in
    Apple_Terminal|iTerm.app|vscode|WezTerm)
      return 0
      ;;
  esac

  return 1
}

terminal_columns() {
  local cols=""

  if command -v tput >/dev/null 2>&1; then
    cols="$(tput cols 2>/dev/null || true)"
  fi
  if [ -z "$cols" ]; then
    cols="${COLUMNS:-}"
  fi

  case "$cols" in
    ''|*[!0-9]*)
      cols=80
      ;;
  esac
  if [ "$cols" -lt 20 ]; then
    cols=20
  fi

  printf '%s\n' "$cols"
}

truncate_text() {
  local value="$1"
  local max_width="$2"

  value="${value//$'\r'/ }"
  value="${value//$'\n'/ }"

  if [ "$max_width" -le 0 ]; then
    return
  fi
  if [ "${#value}" -le "$max_width" ]; then
    printf '%s' "$value"
    return
  fi
  if [ "$max_width" -le 3 ]; then
    printf '%.*s' "$max_width" "$value"
    return
  fi

  printf '%.*s...' "$((max_width - 3))" "$value"
}

friendly_progress_label() {
  local label="$1"

  case "$label" in
    *Fetching\ latest\ main\ build\ metadata*|*Fetching\ latest\ release\ version*|*Fetching\ release\ metadata*)
      printf 'fetching metadata'
      ;;
    *Preparing\ opensre*)
      printf 'resolving build'
      ;;
    *Downloading\ release\ archive*)
      printf 'downloading archive'
      ;;
    *Downloading\ and\ verifying\ checksum*|*Verifying\ release\ archive*)
      printf 'verifying checksum'
      ;;
    *Extracting\ and\ verifying\ binary*)
      printf 'verifying binary'
      ;;
    *Installing\ *opensre*)
      printf 'installing binary'
      ;;
    *)
      label="${label#*\] }"
      printf '%s' "$label"
      ;;
  esac
}

progress_frame() {
  local step_count="$1"

  if terminal_supports_unicode; then
    case $((step_count % 10)) in
      0) printf '⠋' ;;
      1) printf '⠙' ;;
      2) printf '⠹' ;;
      3) printf '⠸' ;;
      4) printf '⠼' ;;
      5) printf '⠴' ;;
      6) printf '⠦' ;;
      7) printf '⠧' ;;
      8) printf '⠇' ;;
      *) printf '⠏' ;;
    esac
    return
  fi

  case $((step_count % 4)) in
    0) printf '-' ;;
    1) printf '\\' ;;
    2) printf '|' ;;
    *) printf '/' ;;
  esac
}

draw_progress() {
  local label="$1"
  local step_count="$2"
  local title="${3:-Installing OpenSRE}"
  local frame
  local columns
  local reserve
  local available
  local width
  local label_width
  local short_label
  local trail=8
  local head
  local bar=""
  local i=0

  columns="$(terminal_columns)"
  if [ "$columns" -lt 56 ]; then
    title="OpenSRE"
  fi

  reserve=$((2 + 1 + 1 + 1 + ${#title} + 1))
  available=$((columns - reserve))
  if [ "$available" -lt 12 ]; then
    width=4
  else
    width=$((available / 2))
    if [ "$width" -gt 28 ]; then
      width=28
    fi
    if [ "$width" -lt 8 ]; then
      width=8
    fi
  fi

  label_width=$((columns - reserve - width))
  if [ "$label_width" -lt 8 ] && [ "$width" -gt 4 ]; then
    width=$((columns - reserve - 8))
    if [ "$width" -lt 4 ]; then
      width=4
    fi
    label_width=$((columns - reserve - width))
  fi
  if [ "$label_width" -lt 0 ]; then
    label_width=0
  fi

  short_label="$(truncate_text "$(friendly_progress_label "$label")" "$label_width")"
  frame="$(progress_frame "$step_count")"
  head=$((step_count % (width + trail)))

  while [ "$i" -lt "$width" ]; do
    local age=$((head - i))
    if [ "$age" -ge 0 ] && [ "$age" -lt "$trail" ]; then
      if terminal_supports_unicode; then
        case "$age" in
          0|1) bar="${bar}${COLOR_GREEN:-}█${COLOR_RESET:-}" ;;
          2|3) bar="${bar}${COLOR_CYAN:-}█${COLOR_RESET:-}" ;;
          4|5) bar="${bar}${COLOR_RED:-}█${COLOR_RESET:-}" ;;
          *) bar="${bar}${COLOR_YELLOW:-}█${COLOR_RESET:-}" ;;
        esac
      else
        bar="${bar}#"
      fi
    else
      if terminal_supports_unicode; then
        bar="${bar}${COLOR_DIM:-}░${COLOR_RESET:-}"
      else
        bar="${bar}-"
      fi
    fi
    i=$((i + 1))
  done

  printf '\r\033[K  %s%s%s %s %s%s%s %s' \
    "${COLOR_YELLOW:-}" "$frame" "${COLOR_RESET:-}" \
    "$bar" "${COLOR_BOLD:-}" "$title" "${COLOR_RESET:-}" "$short_label"
}

animate_progress() {
  local label="$1"
  local step_count=0

  while :; do
    draw_progress "$label" "$step_count"
    step_count=$((step_count + 1))
    sleep 0.08
  done
}

finish_progress() {
  local progress_pid="${1:-}"

  if [ -n "$progress_pid" ]; then
    kill "$progress_pid" 2>/dev/null || true
    wait "$progress_pid" 2>/dev/null || true
  fi
  printf '\r\033[K\033[?25h'
}

run_with_progress() {
  local label="$1"
  shift

  if ! is_interactive_terminal; then
    step "$label"
    "$@"
    return
  fi

  local log_file
  local command_pid
  local status
  log_file="$(mktemp "${TMPDIR:-/tmp}/opensre-install-progress.XXXXXX")"

  "$@" >"$log_file" 2>&1 &
  command_pid=$!

  printf '\033[?25l'
  animate_progress "$label" &
  PROGRESS_PID=$!
  trap 'kill "$command_pid" 2>/dev/null || true; finish_progress "$PROGRESS_PID"; rm -f "$log_file"; exit 130' INT TERM

  if wait "$command_pid"; then
    status=0
  else
    status=$?
  fi

  finish_progress "$PROGRESS_PID"
  PROGRESS_PID=""
  trap - INT TERM

  if [ "$status" -ne 0 ]; then
    printf '%sError:%s %s failed.%s\n' "${COLOR_RED:-}" "${COLOR_RESET:-}" "$label" "${COLOR_RESET:-}" >&2
    cat "$log_file" >&2
    rm -f "$log_file"
    return "$status"
  fi

  rm -f "$log_file"
  printf '  %s%s%s %s\n' "${COLOR_GREEN:-}" "${SUCCESS_MARK:-ok}" "${COLOR_RESET:-}" "$label"
}

capture_with_progress() {
  local __result_var="$1"
  local label="$2"
  shift 2

  if ! is_interactive_terminal; then
    step "$label"
    local captured
    local status
    if captured="$("$@")"; then
      printf -v "$__result_var" '%s' "$captured"
      return
    else
      status=$?
    fi

    if [ -n "$captured" ]; then
      printf '%s\n' "$captured" >&2
    fi
    return "$status"
  fi

  local stdout_file
  local stderr_file
  local command_pid
  local status
  stdout_file="$(mktemp "${TMPDIR:-/tmp}/opensre-install-stdout.XXXXXX")"
  stderr_file="$(mktemp "${TMPDIR:-/tmp}/opensre-install-stderr.XXXXXX")"

  "$@" >"$stdout_file" 2>"$stderr_file" &
  command_pid=$!

  printf '\033[?25l'
  animate_progress "$label" &
  PROGRESS_PID=$!
  trap 'kill "$command_pid" 2>/dev/null || true; finish_progress "$PROGRESS_PID"; rm -f "$stdout_file" "$stderr_file"; exit 130' INT TERM

  if wait "$command_pid"; then
    status=0
  else
    status=$?
  fi

  finish_progress "$PROGRESS_PID"
  PROGRESS_PID=""
  trap - INT TERM

  if [ "$status" -ne 0 ]; then
    printf '%sError:%s %s failed.%s\n' "${COLOR_RED:-}" "${COLOR_RESET:-}" "$label" "${COLOR_RESET:-}" >&2
    cat "$stdout_file" >&2
    cat "$stderr_file" >&2
    rm -f "$stdout_file" "$stderr_file"
    return "$status"
  fi

  printf -v "$__result_var" '%s' "$(cat "$stdout_file")"
  rm -f "$stdout_file" "$stderr_file"
  printf '  %s%s%s %s\n' "${COLOR_GREEN:-}" "${SUCCESS_MARK:-ok}" "${COLOR_RESET:-}" "$label"
}

print_installer_header() {
  if ! is_interactive_terminal; then
    return
  fi

  log "${COLOR_BOLD:-}${COLOR_CYAN:-}OpenSRE Installer${COLOR_RESET:-}"
  log "${COLOR_BOLD:-}Installing the OpenSRE CLI${COLOR_RESET:-}"
  log ""
}

usage() {
  cat <<'EOF'
Usage: install.sh [--main] [--release] [--version <version>] [--install-dir <path>]

Installs the OpenSRE CLI.

Options:
  --main                Install the latest build published from the main branch (default).
  --release             Install the latest versioned release instead of main.
  --version <version>   Install a specific versioned release (for example 2026.4.29).
  --install-dir <path>  Install into a specific directory.
  -h, --help            Show this help text.

Examples:
  curl -fsSL https://install.opensre.com | bash
  curl -fsSL https://install.opensre.com | bash -s -- --main
  curl -fsSL https://install.opensre.com | bash -s -- --version 2026.4.29
EOF
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --main)
        INSTALL_CHANNEL="main"
        INSTALL_CHANNEL_EXPLICIT=1
        ;;
      --release)
        INSTALL_CHANNEL="release"
        INSTALL_CHANNEL_EXPLICIT=1
        ;;
      --version)
        [ "$#" -ge 2 ] || die "--version requires a value."
        requested_version="${2#v}"
        shift
        ;;
      --install-dir)
        [ "$#" -ge 2 ] || die "--install-dir requires a value."
        INSTALL_DIR="$2"
        INSTALL_DIR_OVERRIDE=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
    shift
  done

  case "$INSTALL_CHANNEL" in
    release|main) ;;
    *)
      die "Unsupported install channel: ${INSTALL_CHANNEL}"
      ;;
  esac

  if [ -n "$requested_version" ] && [ "$INSTALL_CHANNEL" = "main" ] && [ "$INSTALL_CHANNEL_EXPLICIT" -eq 0 ]; then
    INSTALL_CHANNEL="release"
  fi

  if [ "$INSTALL_CHANNEL" = "main" ] && [ -n "$requested_version" ]; then
    die "--version cannot be combined with --main."
  fi
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but was not found in PATH."
}

require_prerequisites() {
  need_cmd curl
  need_cmd grep
  need_cmd sed
  need_cmd tr
  need_cmd uname
}

CURL_FLAGS=(
  --fail
  --silent
  --show-error
  --location
  --retry 3
  --retry-delay 1
)

download_to() {
  local url="$1"
  local destination="$2"

  curl "${CURL_FLAGS[@]}" -o "$destination" "$url"
}

download_text() {
  local url="$1"

  curl "${CURL_FLAGS[@]}" \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: opensre-install-script" \
    "$url"
}

fetch_release_json() {
  local version="${1:-}"
  local api_url

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    api_url="https://api.github.com/repos/${REPO}/releases/tags/${MAIN_RELEASE_TAG}"
  elif [ -n "$version" ]; then
    api_url="https://api.github.com/repos/${REPO}/releases/tags/v${version}"
  else
    api_url="https://api.github.com/repos/${REPO}/releases/latest"
  fi

  download_text "$api_url"
}

extract_tag_name() {
  local release_json="$1"

  printf '%s\n' "$release_json" | sed -n '/"tag_name"[[:space:]]*:/{
    s/.*"tag_name":[[:space:]]*"v\{0,1\}\([^"]*\)".*/\1/p
    q
  }'
}

release_has_asset() {
  local release_json="$1"
  local asset_name="$2"

  printf '%s' "$release_json" | tr -d '\r\n\t ' | grep -F "\"name\":\"${asset_name}\"" >/dev/null 2>&1
}

build_archive_name() {
  local version="$1"
  local asset_arch="$2"
  local archive_version="$version"

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    archive_version="main"
  fi

  if [ "$platform" = "windows" ]; then
    printf 'opensre_%s_windows-%s.zip\n' "$archive_version" "$asset_arch"
    return
  fi

  printf 'opensre_%s_%s-%s.tar.gz\n' "$archive_version" "$platform" "$asset_arch"
}

path_has_dir() {
  case ":$PATH:" in
    *":$1:"*)
      return 0
      ;;
  esac

  return 1
}

is_candidate_dir_writable() {
  local dir="$1"
  local parent_dir

  if [ -d "$dir" ]; then
    [ -w "$dir" ]
    return
  fi

  parent_dir="${dir%/*}"
  [ -n "$parent_dir" ] || parent_dir="/"
  [ -d "$parent_dir" ] && [ -w "$parent_dir" ]
}

select_writable_path_candidate_from_list() {
  local candidate_list="$1"
  local old_ifs="$IFS"
  local dir

  IFS=':'
  for dir in $candidate_list; do
    [ -n "$dir" ] || continue
    if path_has_dir "$dir" && is_candidate_dir_writable "$dir"; then
      printf '%s\n' "$dir"
      IFS="$old_ifs"
      return 0
    fi
  done
  IFS="$old_ifs"

  return 1
}

resolve_install_dir() {
  local existing_bin=""
  local existing_dir=""

  if [ -n "$INSTALL_DIR" ]; then
    return
  fi

  if [ "$platform" = "windows" ]; then
    INSTALL_DIR="$DEFAULT_INSTALL_DIR"
    return
  fi

  if command -v opensre >/dev/null 2>&1; then
    existing_bin="$(command -v opensre || true)"
    existing_dir="${existing_bin%/*}"

    if [ -n "$existing_dir" ] && path_has_dir "$existing_dir" && is_candidate_dir_writable "$existing_dir"; then
      INSTALL_DIR="$existing_dir"
      return
    fi
  fi

  if INSTALL_DIR="$(select_writable_path_candidate_from_list "$USER_INSTALL_DIR_CANDIDATES")"; then
    return
  fi

  if INSTALL_DIR="$(select_writable_path_candidate_from_list "$SYSTEM_INSTALL_DIR_CANDIDATES")"; then
    return
  fi

  INSTALL_DIR="$DEFAULT_INSTALL_DIR"
}

ps_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

to_windows_path() {
  local posix_path="$1"

  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$posix_path"
    return
  fi

  die "PowerShell archive extraction requires 'cygpath' when 'unzip' is unavailable."
}

extract_zip() {
  local archive_path="$1"
  local destination_dir="$2"
  local archive_for_ps
  local destination_for_ps

  if command -v unzip >/dev/null 2>&1; then
    unzip -q "$archive_path" -d "$destination_dir"
    return
  fi

  archive_for_ps="$(ps_escape "$(to_windows_path "$archive_path")")"
  destination_for_ps="$(ps_escape "$(to_windows_path "$destination_dir")")"

  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoLogo -NoProfile -NonInteractive -Command \
      "Expand-Archive -LiteralPath '$archive_for_ps' -DestinationPath '$destination_for_ps' -Force" \
      >/dev/null
    return
  fi

  if command -v pwsh >/dev/null 2>&1; then
    pwsh -NoLogo -NoProfile -NonInteractive -Command \
      "Expand-Archive -LiteralPath '$archive_for_ps' -DestinationPath '$destination_for_ps' -Force" \
      >/dev/null
    return
  fi

  die "A zip extractor is required on Windows. Install 'unzip' or run the PowerShell installer."
}

extract_archive() {
  local archive_path="$1"
  local destination_dir="$2"

  if [ "$platform" = "windows" ]; then
    extract_zip "$archive_path" "$destination_dir"
    return
  fi

  need_cmd tar
  tar -xzf "$archive_path" -C "$destination_dir"
}

verify_checksum() {
  local checksum_path="$1"
  local archive_path="$2"
  local archive_dir
  local checksum_name
  local normalized_checksum_path
  local expected
  local actual

  archive_dir="${archive_path%/*}"
  checksum_name="${checksum_path##*/}"
  normalized_checksum_path="${checksum_path}.normalized"

  tr -d '\r' < "$checksum_path" > "$normalized_checksum_path"
  checksum_path="$normalized_checksum_path"
  checksum_name="${checksum_path##*/}"

  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$archive_dir" && sha256sum -c "$checksum_name") >/dev/null \
      || die "Checksum verification failed for '${archive_path##*/}'."
    return
  fi

  if command -v shasum >/dev/null 2>&1; then
    (cd "$archive_dir" && shasum -a 256 -c "$checksum_name") >/dev/null \
      || die "Checksum verification failed for '${archive_path##*/}'."
    return
  fi

  if command -v openssl >/dev/null 2>&1; then
    expected="$(sed -n 's/^\([0-9A-Fa-f]\{64\}\)[[:space:]][[:space:]]*.*/\1/p' "$checksum_path")"
    [ -n "$expected" ] || die "Checksum file '${checksum_name}' is malformed."

    actual="$(openssl dgst -sha256 "$archive_path" | sed 's/^.*= //')"
    [ "$expected" = "$actual" ] || die "Checksum verification failed for '${archive_path##*/}'."
    return
  fi

  warn "No checksum verifier found (sha256sum, shasum, or openssl). Skipping checksum verification."
}

binary_app_root() {
  local binary_path="$1"
  local binary_dir

  binary_dir="${binary_path%/*}"
  if [ -d "${binary_dir}/_internal" ]; then
    printf '%s\n' "$binary_dir"
    return 0
  fi

  return 1
}

install_binary() {
  local source_path="$1"
  local destination_path="$2"

  if command -v install >/dev/null 2>&1; then
    install -m 0755 "$source_path" "$destination_path"
    return
  fi

  cp "$source_path" "$destination_path"
  chmod 0755 "$destination_path" 2>/dev/null || true
}

install_binary_app() {
  local app_root="$1"
  local destination_path="$2"
  local app_destination_dir="${INSTALL_DIR}/.${BIN_NAME}-app"
  local app_tmp_dir="${app_destination_dir}.new.$$"
  local app_old_dir="${app_destination_dir}.old.$$"

  rm -rf "$app_tmp_dir" "$app_old_dir"
  cp -R "$app_root" "$app_tmp_dir"
  chmod -R u+rwX,go+rX "$app_tmp_dir" 2>/dev/null || true

  if [ -e "$app_destination_dir" ]; then
    mv "$app_destination_dir" "$app_old_dir"
  fi
  mv "$app_tmp_dir" "$app_destination_dir"
  rm -rf "$app_old_dir"

  rm -f "$destination_path"
  ln -s "$app_destination_dir/${BIN_NAME}" "$destination_path"
}

install_verified_binary() {
  local source_path="$1"
  local destination_path="$2"
  local app_root=""

  mkdir -p "$INSTALL_DIR"
  if [ "$platform" != "windows" ] && app_root="$(binary_app_root "$source_path")"; then
    install_binary_app "$app_root" "$destination_path"
    return
  fi

  install_binary "$source_path" "$destination_path"
}

download_and_verify_checksum() {
  local checksum_url="$1"
  local checksum_path="$2"
  local archive_path="$3"

  download_to "$checksum_url" "$checksum_path"
  verify_checksum "$checksum_path" "$archive_path"
}

extract_and_verify_binary() {
  local archive_path="$1"
  local extraction_dir="$2"
  local extracted_binary_path
  local extracted_version

  extract_archive "$archive_path" "$extraction_dir"
  extracted_binary_path="$(get_binary_path_from_archive "$extraction_dir" "$BIN_NAME")"
  if [ "$INSTALL_CHANNEL" = "main" ]; then
    extracted_version="$(verify_binary_version "$extracted_binary_path")"
  else
    extracted_version="$(verify_binary_version "$extracted_binary_path" "$version")"
  fi

  printf '%s\n%s\n' "$extracted_binary_path" "$extracted_version"
}

get_binary_path_from_archive() {
  local extraction_root="$1"
  local binary_name="$2"
  local direct_binary_path
  local binary_candidates=()
  local binary_locations

  direct_binary_path="${extraction_root}/${binary_name}"
  if [ -f "$direct_binary_path" ]; then
    printf '%s\n' "$direct_binary_path"
    return
  fi

  need_cmd find

  while IFS= read -r candidate; do
    binary_candidates+=("$candidate")
  done < <(find "$extraction_root" -type f -name "$binary_name")

  case "${#binary_candidates[@]}" in
    1)
      printf '%s\n' "${binary_candidates[0]}"
      ;;
    0)
      die "Archive '${archive}' did not contain '${binary_name}'."
      ;;
    *)
      binary_locations="$(printf '%s, ' "${binary_candidates[@]}")"
      binary_locations="${binary_locations%, }"
      die "Found multiple '${binary_name}' files after extraction: ${binary_locations}"
      ;;
  esac
}

verify_binary_version() {
  local binary_path="$1"
  local expected_version="${2:-}"
  local version_output
  local version_status
  local actual_version

  set +e
  version_output="$("$binary_path" --version 2>&1)"
  version_status=$?
  set -e

  if [ "$version_status" -ne 0 ]; then
    printf 'Failed to execute %s --version (exit %s).\n' "${binary_path##*/}" "$version_status" >&2
    if [ -n "$version_output" ]; then
      printf 'Command output:\n%s\n' "$version_output" >&2
    else
      printf 'Command output: <empty>\n' >&2
    fi
    print_binary_diagnostics "$binary_path"
    return 1
  fi

  actual_version="$(printf '%s\n' "$version_output" | sed -n 's/.*\([0-9][0-9][0-9][0-9]\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -n 1)"

  if [ -z "$expected_version" ]; then
    if [ -n "$actual_version" ]; then
      printf '%s\n' "$actual_version"
    else
      printf 'main\n'
    fi
    return
  fi

  case "$version_output" in
    *"$expected_version"*)
      printf '%s\n' "$expected_version"
      ;;
    *)
      if [ -n "$requested_version" ] || [ -z "$actual_version" ]; then
        die "Downloaded binary version mismatch. Expected '${expected_version}' but got: ${version_output}"
      fi

      warn "Latest release metadata reports v${expected_version}, but the downloaded binary reports v${actual_version}. Installing the verified binary anyway."
      printf '%s\n' "$actual_version"
      ;;
  esac
}

print_binary_diagnostics() {
  local binary_path="$1"

  printf 'Binary diagnostics:\n' >&2
  printf '  path: %s\n' "$binary_path" >&2
  if command -v uname >/dev/null 2>&1; then
    printf '  system: %s\n' "$(uname -a 2>/dev/null || true)" >&2
  fi
  if command -v ls >/dev/null 2>&1; then
    ls -l "$binary_path" >&2 2>/dev/null || true
  fi
  if command -v file >/dev/null 2>&1; then
    file "$binary_path" >&2 2>/dev/null || true
  fi
  if [ "$platform" = "linux" ] && command -v ldd >/dev/null 2>&1; then
    ldd "$binary_path" >&2 2>/dev/null || true
  fi
}

configure_path() {
  case ":$PATH:" in
    *":${INSTALL_DIR}:"*)
      return
      ;;
  esac

  if [ "$platform" = "windows" ]; then
    warn "'${INSTALL_DIR}' is not in PATH for this shell. Add it to Git Bash or Windows PATH to run ${BIN_NAME:-opensre} from any terminal."
    return
  fi

  local rc_file=""
  local path_line=""
  local shell_name
  shell_name="${SHELL##*/}"

  case "$shell_name" in
    zsh)
      rc_file="${HOME}/.zshrc"
      path_line="export PATH=\"${INSTALL_DIR}:\$PATH\""
      ;;
    bash)
      if [ "$platform" = "darwin" ]; then
        rc_file="${HOME}/.bash_profile"
      else
        rc_file="${HOME}/.bashrc"
      fi
      path_line="export PATH=\"${INSTALL_DIR}:\$PATH\""
      ;;
    fish)
      rc_file="${HOME}/.config/fish/config.fish"
      path_line="fish_add_path \"${INSTALL_DIR}\""
      ;;
    *)
      log "Add the following line to your shell profile to use ${BIN_NAME:-opensre}:"
      log "  export PATH=\"${INSTALL_DIR}:\$PATH\""
      return
      ;;
  esac

  local rc_dir="${rc_file%/*}"
  [ "$rc_dir" != "$rc_file" ] && [ ! -d "$rc_dir" ] && mkdir -p "$rc_dir"

  if [ -f "$rc_file" ] && grep -qF "${INSTALL_DIR}" "$rc_file"; then
    return
  fi

  local marker="# Added by opensre installer"
  if [ -f "$rc_file" ] && grep -qF "$marker" "$rc_file" && grep -qF "${INSTALL_DIR}" "$rc_file"; then
    return
  fi

  printf '\n%s\n%s\n' "$marker" "$path_line" >> "$rc_file"

  log ""
  log "${BIN_NAME:-opensre} has been added to PATH in ${rc_file}."
  log "To apply now, run:  source \"${rc_file}\""
  log "Or open a new terminal."
}

print_success_screen() {
  local version="$1"
  local sep="────────────────────────────────────────────"

  if [ ! -t 1 ]; then
    sep="--------------------------------------------"
  fi

  log ""
  log "$sep"
  success "Welcome to OpenSRE"
  if [ "$version" = "main" ]; then
    log "  ${COLOR_BOLD:-}opensre (main build) installed successfully${COLOR_RESET:-}"
  else
    log "  ${COLOR_BOLD:-}opensre v${version} installed successfully${COLOR_RESET:-}"
  fi
  log "$sep"
  log ""
  log "Next steps:"
  log "  1. Run  ${BIN_NAME:-opensre} onboard"
  log "     Set up your LLM provider and add your observability integrations."
  log ""
  log "  2. Run  ${BIN_NAME:-opensre}  (no subcommand)"
  log "     From a normal interactive terminal this starts the interactive shell — type a"
  log "     prompt or incident description at the prompt to investigate."
  log ""
  log "  3. Optional — one-shot RCA from a file:"
  log "     ${BIN_NAME:-opensre} investigate -i path/to/alert.json"
  log ""
  log "Docs: https://www.opensre.com/docs"
  log ""
}

auto_launch_disabled() {
  case "${OPENSRE_AUTO_LAUNCH:-}" in
    0|false|FALSE|no|NO|off|OFF)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

launch_onboarding_after_install() {
  if auto_launch_disabled; then
    return
  fi

  # Only auto-launch the interactive wizard when the installer itself is
  # attached to a real terminal on both stdin and stdout. When the installer is
  # piped (the documented `curl … | bash`), stdin is the pipe rather than a
  # terminal, so onboarding's full-screen prompt cannot reliably take control of
  # the terminal and exits with a "terminal I/O error" mid-render (issue #3273).
  # In that case we skip the launch; the "Next steps" hint already tells the user
  # to run `${BIN_NAME} onboard` in their own terminal, where it works.
  if [ ! -t 0 ] || [ ! -t 1 ]; then
    return
  fi

  local installed_binary="${INSTALL_DIR}/${BIN_NAME}"
  if [ ! -x "$installed_binary" ]; then
    warn "Could not auto-launch onboarding; ${installed_binary} is not executable."
    return
  fi

  log "Launching ${BIN_NAME} onboard..."
  "$installed_binary" onboard || \
    warn "Onboarding exited before completion. Run '${BIN_NAME} onboard' to retry."
}

cleanup() {
  if [ -n "${tmp_dir:-}" ] && [ -d "$tmp_dir" ]; then
    rm -rf "$tmp_dir"
  fi
}

detect_platform() {
  local os
  local arch

  os="$(uname -s)"
  arch="$(uname -m)"

  case "$os" in
    Linux)
      platform="linux"
      ;;
    Darwin)
      platform="darwin"
      ;;
    MINGW*|MSYS*|CYGWIN*)
      platform="windows"
      BIN_NAME="opensre.exe"
      log "Detected Windows environment (${os})."
      ;;
    *)
      die "Unsupported operating system: $os"
      ;;
  esac

  case "$arch" in
    x86_64|amd64)
      target_arch="x64"
      ;;
    arm64|aarch64)
      target_arch="arm64"
      ;;
    *)
      die "Unsupported architecture: $arch"
      ;;
  esac
}

resolve_release_metadata() {
  version="$requested_version"
  release_tag=""

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    metadata_step="[1/6] Fetching latest main build metadata"
  elif [ -n "$version" ]; then
    metadata_step="[1/6] Fetching release metadata for v${version}"
  else
    metadata_step="[1/6] Fetching latest release version"
  fi

  capture_with_progress release_json "$metadata_step" fetch_release_json "$version" || {
    if [ "$INSTALL_CHANNEL" = "main" ]; then
      die "Failed to query main build metadata from GitHub."
    fi

    die "Failed to query release metadata from GitHub."
  }

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    release_tag="$(extract_tag_name "$release_json")"
  else
    if [ -z "$version" ]; then
      version="$(extract_tag_name "$release_json")"
    fi
    release_tag="v${version}"
  fi

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    [ -n "$release_tag" ] || die "Failed to determine the main build tag."
  else
    [ -n "$version" ] || die "Failed to determine the release version."
  fi
}

select_archive_asset() {
  local fallback_archive

  asset_arch="$target_arch"
  archive="$(build_archive_name "$version" "$asset_arch")"

  if [ "$platform" = "windows" ] && [ "$target_arch" = "arm64" ] && ! release_has_asset "$release_json" "$archive"; then
    fallback_archive="$(build_archive_name "$version" "x64")"

    if release_has_asset "$release_json" "$fallback_archive"; then
      asset_arch="x64"
      archive="$fallback_archive"
      warn "Windows ARM64 artifact is not published for v${version}; falling back to the x64 build."
    fi
  fi

  if release_has_asset "$release_json" "$archive"; then
    return
  fi

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    die "Main build release does not include asset '${archive}'."
  fi

  die "Release v${version} does not include asset '${archive}'."
}

prepare_download() {
  download_url="https://github.com/${REPO}/releases/download/${release_tag}/${archive}"
  checksum_asset="${archive}.sha256"
  checksum_url="${download_url}.sha256"

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    step "[2/6] Preparing opensre main build (${platform}/${target_arch})"
  else
    step "[2/6] Preparing opensre v${version} (${platform}/${target_arch})"
  fi
  if [ "$asset_arch" != "$target_arch" ]; then
    log "Using release asset built for ${platform}/${asset_arch}."
  fi
  if install_verbose; then
    log "  ${download_url}"
  fi
}

create_temp_workspace() {
  need_cmd mktemp
  tmp_dir="$(mktemp -d)"
  trap cleanup EXIT
}

download_release_archive() {
  archive_path="${tmp_dir}/${archive}"
  run_with_progress "[3/6] Downloading release archive (${archive})" download_to "$download_url" "$archive_path" \
    || die "Failed to download '${archive}'."
}

verify_release_checksum() {
  local checksum_path

  if release_has_asset "$release_json" "$checksum_asset"; then
    checksum_path="${tmp_dir}/${checksum_asset}"
    run_with_progress "[4/6] Downloading and verifying checksum (${checksum_asset})" \
      download_and_verify_checksum "$checksum_url" "$checksum_path" "$archive_path" \
      || die "Failed to download or verify checksum '${checksum_asset}'."
    return
  fi

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    warn "Main build release is missing checksum asset '${checksum_asset}'."
  else
    warn "Release v${version} is missing checksum asset '${checksum_asset}'."
  fi
}

extract_release_binary() {
  local verified_binary

  capture_with_progress verified_binary "[5/6] Extracting and verifying binary" extract_and_verify_binary "$archive_path" "$tmp_dir"
  binary_path="${verified_binary%%$'\n'*}"
  installed_version="${verified_binary#*$'\n'}"
}

install_release_binary() {
  run_with_progress "[6/6] Installing ${BIN_NAME} to ${INSTALL_DIR}" install_verified_binary "$binary_path" "${INSTALL_DIR}/${BIN_NAME}"
}

print_install_confirmation() {
  if [ "$INSTALL_CHANNEL" = "main" ]; then
    if [ "$installed_version" = "main" ]; then
      success "Installed ${BIN_NAME} main build to ${INSTALL_DIR}/${BIN_NAME}"
    else
      success "Installed ${BIN_NAME} main build (${installed_version}) to ${INSTALL_DIR}/${BIN_NAME}"
    fi
  else
    success "Installed ${BIN_NAME} v${installed_version} to ${INSTALL_DIR}/${BIN_NAME}"
  fi
}

finish_install() {
  print_install_confirmation
  configure_path
  print_success_screen "$installed_version"
  launch_onboarding_after_install
}

main() {
  parse_args "$@"
  require_prerequisites
  detect_platform
  resolve_install_dir
  print_installer_header
  resolve_release_metadata
  select_archive_asset
  prepare_download
  create_temp_workspace
  download_release_archive
  verify_release_checksum
  extract_release_binary
  install_release_binary
  finish_install
}

main "$@"
