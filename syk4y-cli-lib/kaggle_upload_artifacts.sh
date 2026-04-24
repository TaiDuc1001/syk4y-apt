# Artifact discovery and validation helpers for `syk4y kaggle upload`.

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
  printf '%s/%s\n' "$UPLOAD_ROOT" "$(artifact_dataset_slug "$artifact_id")"
}

artifact_metadata_file() {
  local artifact_id="$1"
  printf '%s/dataset-metadata.json\n' "$(artifact_dataset_dir "$artifact_id")"
}

artifact_source_path() {
  local artifact_id="$1"
  local source_spec
  if [[ "$artifact_id" == "wheelhouse" ]]; then
    printf '%s\n' "$WHEELHOUSE_PATH"
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
  local -a artifact_settings
  declare -A seen_artifact_ids=()

  ARTIFACT_IDS=()
  ARTIFACT_SOURCE_SPEC=()
  ARTIFACT_ITEM_NAMES=()
  prefix="${BASE_DATASET_SLUG}-"

  while IFS= read -r dataset_dir; do
    dataset_slug="$(basename "$dataset_dir")"
    artifact_id="${dataset_slug#$prefix}"
    if [[ -z "$artifact_id" ]]; then
      continue
    fi
    metadata_file="$dataset_dir/dataset-metadata.json"
    if [[ ! -f "$metadata_file" ]]; then
      continue
    fi

    if [[ -n "${seen_artifact_ids[$artifact_id]+x}" ]]; then
      echo "Error: duplicate initialized artifact id '$artifact_id' under '$UPLOAD_ROOT'." >&2
      exit 1
    fi
    seen_artifact_ids["$artifact_id"]=1

    mapfile -t artifact_settings < <(
      "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" read-artifact-settings "$metadata_file"
    )
    source_spec="${artifact_settings[0]:-}"
    item_name="${artifact_settings[1]:-}"

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
    ARTIFACT_SOURCE_SPEC["$artifact_id"]="$source_spec"
    ARTIFACT_ITEM_NAMES["$artifact_id"]="$item_name"
  done < <(find "$UPLOAD_ROOT" -mindepth 1 -maxdepth 1 -type d -name "${BASE_DATASET_SLUG}-*" -printf '%p\n' 2>/dev/null | LC_ALL=C sort)

  if [[ "${#ARTIFACT_IDS[@]}" -eq 0 ]]; then
    echo "Error: no initialized artifacts found under '$UPLOAD_ROOT'." >&2
    echo "Run: syk4y init <artifact...> first." >&2
    exit 1
  fi

  echo "Detected initialized artifacts: ${ARTIFACT_IDS[*]}"
}

verify_dataset_structure() {
  local artifact_id dataset_dir metadata_file item_name path
  for artifact_id in "${ARTIFACT_IDS[@]}"; do
    dataset_dir="$(artifact_dataset_dir "$artifact_id")"
    metadata_file="$(artifact_metadata_file "$artifact_id")"
    if [[ ! -f "$metadata_file" ]]; then
      echo "Error: missing '$metadata_file'. Run ./make-gen-full-repo.sh first." >&2
      exit 1
    fi

    if [[ "$artifact_id" == "wheelhouse" ]]; then
      if [[ ! -f "$WHEELHOUSE_PATH" ]]; then
        echo "Error: missing '$WHEELHOUSE_PATH' after wheelhouse build." >&2
        exit 1
      fi
      continue
    fi

    item_name="$(artifact_item_name "$artifact_id")"
    path="$dataset_dir/$item_name"
    if [[ ! -e "$path" ]]; then
      echo "Error: missing artifact link '$path'. Run ./make-gen-full-repo.sh first." >&2
      exit 1
    fi
    if [[ ! -L "$path" ]]; then
      echo "Error: '$path' must be a symlink. Run ./make-gen-full-repo.sh to re-create structure." >&2
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

  if printf '%s\n' "$csv_output" | awk -F',' 'NR > 1 {print $1}' | grep -Fxq "$artifact_item_name"; then
    echo ""
  else
    echo "$artifact_item_name"
  fi
}
