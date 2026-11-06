# Artifact discovery and validation helpers for `syk4y kaggle upload`.

# shellcheck source=/dev/null
source "${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}/syk4y-cli-lib/string_utils.sh"

declare -g -A ARTIFACT_DATASET_DIRS=()

artifact_item_name() {
  local artifact_id="$1"
  local item_name="${ARTIFACT_ITEM_NAMES[$artifact_id]:-}"
  if [[ -z "$item_name" ]]; then
    if [[ "$artifact_id" == "wheelhouse" ]]; then
      item_name="wheelhouse.zip"
    else
      item_name="$artifact_id"
    fi
  fi
  printf '%s\n' "$item_name"
}

artifact_dataset_slug() {
  local artifact_id="$1"
  printf '%s-%s\n' "$BASE_DATASET_SLUG" "$artifact_id"
}

artifact_dataset_dir() {
  local artifact_id="$1"
  if [[ -n "${ARTIFACT_DATASET_DIRS[$artifact_id]:-}" ]]; then
    printf '%s\n' "${ARTIFACT_DATASET_DIRS[$artifact_id]}"
  else
    printf '%s/%s\n' "$UPLOAD_ROOT" "$(artifact_dataset_slug "$artifact_id")"
  fi
}

artifact_metadata_file() {
  local artifact_id="$1"
  printf '%s/dataset-metadata.json\n' "$(artifact_dataset_dir "$artifact_id")"
}

artifact_source_path() {
  local artifact_id="$1"
  local source_spec
  if [[ "$artifact_id" == "wheelhouse" ]]; then
    local ds_dir
    ds_dir="$(artifact_dataset_dir "$artifact_id")"
    if [[ -f "$ds_dir/wheelhouse.zip" ]]; then
      printf '%s\n' "$ds_dir/wheelhouse.zip"
    else
      printf '%s\n' "$WHEELHOUSE_PATH"
    fi
  else
    source_spec="${ARTIFACT_SOURCE_SPEC[$artifact_id]:-$artifact_id}"
    if [[ "$source_spec" == /* ]]; then
      printf '%s\n' "$source_spec"
    else
      printf '%s\n' "$REPO_ROOT/$source_spec"
    fi
  fi
}

resolve_initialized_artifacts() {
  local artifact_id metadata_file dataset_dir dataset_slug prefix source_spec item_name
  local -a artifact_settings selected_artifact_ids missing_artifacts
  declare -A seen_artifact_ids=()
  declare -A artifact_alias_map=()
  selected_artifact_ids=()
  missing_artifacts=()

  ARTIFACT_IDS=()
  ALL_ARTIFACT_IDS=()
  ARTIFACT_SOURCE_SPEC=()
  ARTIFACT_ITEM_NAMES=()
  ARTIFACT_DATASET_DIRS=()

  # Ensure UPLOAD_ROOT is an absolute canonical path
  if [[ -d "$UPLOAD_ROOT" ]]; then
    UPLOAD_ROOT="$(cd "$UPLOAD_ROOT" 2>/dev/null && pwd -P)"
  fi

  prefix="${BASE_DATASET_SLUG}-"

  if [[ -d "$UPLOAD_ROOT" ]]; then
    while IFS= read -r -d '' dataset_dir; do
      [[ -d "$dataset_dir" ]] || continue
      dataset_slug="$(basename "$dataset_dir")"

      # Skip hidden directories and nested kaggle_upload directory
      if [[ "$dataset_slug" == .* ]] || [[ "$dataset_slug" == "kaggle_upload" ]]; then
        continue
      fi

      metadata_file="$dataset_dir/dataset-metadata.json"

      # Check candidate artifact directory
      if [[ "$dataset_slug" == "${prefix}"* ]] || [[ -f "$metadata_file" ]]; then
        if [[ ! -f "$metadata_file" ]]; then
          echo "Error: artifact directory '$dataset_dir' is missing required metadata file 'dataset-metadata.json'." >&2
          echo "Run: syk4y init <artifact...> first." >&2
          exit 1
        fi

        # Validate metadata integrity
        if ! "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" validate-metadata "$metadata_file"; then
          echo "Error: artifact metadata in '$metadata_file' is invalid or corrupt." >&2
          exit 1
        fi

        mapfile -t artifact_settings < <(
          "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" read-artifact-settings "$metadata_file"
        )
        source_spec="${artifact_settings[0]:-}"
        item_name="${artifact_settings[1]:-}"
        local meta_artifact_id="${artifact_settings[2]:-}"

        if [[ -n "$meta_artifact_id" ]]; then
          artifact_id="$meta_artifact_id"
        elif [[ "$dataset_slug" == "${prefix}"* ]]; then
          artifact_id="${dataset_slug#$prefix}"
        elif [[ "$dataset_slug" == "kaggle-upload-wheelhouse" || "$dataset_slug" == *"-wheelhouse" || "$dataset_slug" == "wheelhouse" ]]; then
          artifact_id="wheelhouse"
        else
          artifact_id="$dataset_slug"
        fi

        if [[ -z "$artifact_id" ]]; then
          continue
        fi

        if [[ -n "${seen_artifact_ids[$artifact_id]+x}" ]]; then
          echo "Error: duplicate initialized artifact id '$artifact_id' under '$UPLOAD_ROOT'." >&2
          exit 1
        fi
        seen_artifact_ids["$artifact_id"]=1

        # Register aliases for flexible filtering
        artifact_alias_map["$artifact_id"]="$artifact_id"
        artifact_alias_map["$dataset_slug"]="$artifact_id"
        if [[ "$dataset_slug" == "${prefix}"* ]]; then
          artifact_alias_map["${dataset_slug#$prefix}"]="$artifact_id"
        fi
        if [[ "$artifact_id" == "wheelhouse" || "$dataset_slug" == *"wheelhouse"* ]]; then
          artifact_alias_map["wheelhouse"]="$artifact_id"
          artifact_alias_map["kaggle-upload-wheelhouse"]="$artifact_id"
        fi
        if [[ -n "$source_spec" ]]; then
          artifact_alias_map["$source_spec"]="$artifact_id"
          artifact_alias_map["$(basename "$source_spec")"]="$artifact_id"
          artifact_alias_map["$(syk4y_slugify "$source_spec")"]="$artifact_id"
        fi

        if [[ "$artifact_id" == "wheelhouse" ]]; then
          source_spec=""
          if [[ -z "$item_name" ]]; then
            item_name="wheelhouse.zip"
          fi
        else
          if [[ -z "$source_spec" ]]; then
            source_spec="$artifact_id"
          fi
          if [[ -z "$item_name" ]]; then
            item_name="$(basename "$source_spec")"
          fi
          if [[ -z "$item_name" ]] || [[ "$item_name" == "." ]] || [[ "$item_name" == "/" ]]; then
            item_name="$artifact_id"
          fi
        fi

        ARTIFACT_IDS+=("$artifact_id")
        ARTIFACT_DATASET_DIRS["$artifact_id"]="$dataset_dir"
        ARTIFACT_SOURCE_SPEC["$artifact_id"]="$source_spec"
        ARTIFACT_ITEM_NAMES["$artifact_id"]="$item_name"
      fi
    done < <(find "$UPLOAD_ROOT" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null | LC_ALL=C sort -z)
  fi

  if [[ "${#ARTIFACT_IDS[@]}" -eq 0 ]]; then
    if [[ -d "$UPLOAD_ROOT/kaggle_upload" ]] && find "$UPLOAD_ROOT/kaggle_upload" -mindepth 1 -maxdepth 2 -type f -name 'dataset-metadata.json' 2>/dev/null | grep -q .; then
      echo "Error: no initialized artifacts found directly under '$UPLOAD_ROOT'." >&2
      echo "Detected legacy nested artifact directory at: '$UPLOAD_ROOT/kaggle_upload'." >&2
      echo "This layout occurred because syk4y commands were previously executed from inside 'kaggle_upload/'." >&2
      echo "To safely inspect and migrate your existing wheelhouse and artifacts without rebuilding:" >&2
      echo "  syk4y migrate-layout --dry-run" >&2
      echo "  syk4y migrate-layout" >&2
      exit 1
    fi
    echo "Error: no initialized artifacts found under '$UPLOAD_ROOT'." >&2
    echo "Run: syk4y init <artifact...> first." >&2
    exit 1
  fi

  ALL_ARTIFACT_IDS=("${ARTIFACT_IDS[@]}")

  if [[ "${#ARTIFACT_FILTER_IDS[@]}" -gt 0 ]]; then
    declare -A chosen_ids=()
    for filter in "${ARTIFACT_FILTER_IDS[@]}"; do
      local matched_id="${artifact_alias_map[$filter]:-}"
      if [[ -z "$matched_id" ]]; then
        missing_artifacts+=("$filter")
      elif [[ -z "${chosen_ids[$matched_id]+x}" ]]; then
        chosen_ids["$matched_id"]=1
        selected_artifact_ids+=("$matched_id")
      fi
    done

    if [[ "${#missing_artifacts[@]}" -gt 0 ]]; then
      echo "Error: requested artifact(s) were not initialized under '$UPLOAD_ROOT': ${missing_artifacts[*]}" >&2
      echo "Initialized artifacts: ${ALL_ARTIFACT_IDS[*]}" >&2
      echo "Run: syk4y init <artifact...> first." >&2
      exit 1
    fi

    ARTIFACT_IDS=("${selected_artifact_ids[@]}")
    echo "Detected initialized artifacts: ${ALL_ARTIFACT_IDS[*]}"
    echo "Selected artifacts for upload: ${ARTIFACT_IDS[*]}"
    return
  fi

  echo "Detected initialized artifacts: ${ARTIFACT_IDS[*]}"
}

verify_dataset_structure() {
  local artifact_id dataset_dir metadata_file item_name path
  for artifact_id in "${ARTIFACT_IDS[@]}"; do
    dataset_dir="$(artifact_dataset_dir "$artifact_id")"
    metadata_file="$(artifact_metadata_file "$artifact_id")"
    if [[ ! -f "$metadata_file" ]]; then
      echo "Error: missing '$metadata_file'. Run syk4y init first." >&2
      exit 1
    fi

    if [[ "$artifact_id" == "wheelhouse" ]]; then
      local wh_source
      wh_source="$(artifact_source_path "wheelhouse")"
      if [[ ! -f "$wh_source" ]]; then
        echo "Error: missing wheelhouse archive '$wh_source'. Run syk4y init wheelhouse to build." >&2
        exit 1
      fi
      continue
    fi

    item_name="$(artifact_item_name "$artifact_id")"
    path="$dataset_dir/$item_name"
    if [[ ! -e "$path" ]]; then
      echo "Error: missing artifact item '$path'. Run syk4y init first." >&2
      exit 1
    fi
  done
}

extract_dataset_ref() {
  local metadata_file="$1"
  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" extract-dataset-ref "$metadata_file"
}

remote_missing_expected_artifacts() {
  local dataset_ref="$1"
  local artifact_id="$2"
  local artifact_item_name="$3"

  if [[ "$artifact_id" != "wheelhouse" ]]; then
    echo ""
    return
  fi

  local csv_output
  csv_output="$("${KAGGLE_CMD[@]}" datasets files -d "$dataset_ref" --csv --page-size 200 2>/dev/null || true)"
  if [[ -z "$csv_output" ]]; then
    echo ""
    return
  fi

  if [[ "$(printf '%s\n' "$csv_output" | "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" csv-first-column-contains "$artifact_item_name")" == "1" ]]; then
    echo ""
  else
    echo "$artifact_item_name"
  fi
}
