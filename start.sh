#!/bin/bash
# Запуск OpenClaw
cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
    echo "❌ Файл .env не найден!"
    echo "👉 Скопируй .env.example в .env и заполни все значения:"
    echo "   cp .env.example .env"
    echo "   open .env"
    exit 1
fi

echo "🚀 Запуск OpenClaw..."
source venv/bin/activate
python main.py
