#!/bin/bash
# Quick start script for SWE-bench Lite evaluation

set -e

echo "================================================================================"
echo "SWE-bench Lite Evaluation - Quick Start"
echo "================================================================================"
echo ""

# Check if GEMINI_API_KEY is set
if [ -z "$GEMINI_API_KEY" ]; then
    echo "ERROR: GEMINI_API_KEY environment variable is not set."
    echo ""
    echo "Please set it with:"
    echo "  export GEMINI_API_KEY='your-api-key'"
    echo ""
    exit 1
fi

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python version: $python_version"

# Install datasets if not already installed
echo ""
echo "Checking dependencies..."
if ! python3 -c "import datasets" 2>/dev/null; then
    echo "Installing 'datasets' package..."
    pip3 install datasets
else
    echo "datasets package already installed"
fi

echo ""
echo "Starting evaluation with default settings:"
echo "  - 10 instances"
echo "  - Model: gemini-2.5-flash"
echo "  - Max steps: 15"
echo "  - Output: data/swebench_eval/"
echo ""
echo "This will take approximately 1-2 hours."
echo ""
read -p "Press Enter to continue or Ctrl+C to cancel..."

# Run evaluation
python3 run_swebench_eval.py \
    --num-instances 10 \
    --model gemini-2.5-flash \
    --max-steps 15 \
    --phase autopilot \
    --interaction-mode none

echo ""
echo "================================================================================"
echo "Evaluation complete!"
echo "Check results in: data/swebench_eval/"
echo "================================================================================"
