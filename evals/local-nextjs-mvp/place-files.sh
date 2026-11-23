#!/usr/bin/env bash
set -eu
cp -R "$(dirname "$0")/files/." .
# bracketed folder names don't survive skill packaging, so the dynamic route is created here
mkdir -p "next-mvp/app/dashboard/projects/[id]"
echo "export default function Page(){return null}" > "next-mvp/app/dashboard/projects/[id]/page.tsx"
