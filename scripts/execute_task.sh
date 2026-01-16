#!/usr/bin/env bash
set -u

STEP_DIR="${STEP_DIR:?STEP_DIR must be set}"
TASK="${TASK:-}"
CONTEXT="${CONTEXT:-}"
CONSTRAINTS="${CONSTRAINTS:-}"
SUCCESS_CRITERIA="${SUCCESS_CRITERIA:-}"
COMMAND="${COMMAND:-}"
WORKSPACE_DIR="${WORKSPACE_DIR:-}"

mkdir -p "${STEP_DIR}"

printf "%s" "${TASK}" > "${STEP_DIR}/task.txt"
printf "%s" "${CONTEXT}" > "${STEP_DIR}/context.txt"
printf "%s" "${CONSTRAINTS}" > "${STEP_DIR}/constraints.txt"
printf "%s" "${SUCCESS_CRITERIA}" > "${STEP_DIR}/success_criteria.txt"
printf "%s\n" "${COMMAND}" > "${STEP_DIR}/command.txt"
printf "%s" "${WORKSPACE_DIR}" > "${STEP_DIR}/workspace_dir.txt"
pwd > "${STEP_DIR}/pwd.txt"

if [ -z "${COMMAND}" ]; then
  echo "Missing COMMAND" > "${STEP_DIR}/stderr.txt"
  echo "1" > "${STEP_DIR}/returncode.txt"
  exit 0
fi

bash -lc "${COMMAND}" > "${STEP_DIR}/stdout.txt" 2> "${STEP_DIR}/stderr.txt"
echo "$?" > "${STEP_DIR}/returncode.txt"
