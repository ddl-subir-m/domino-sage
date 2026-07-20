# Conventions for the coding agent

This is a warm React + TypeScript + Vite starter. Dependencies are already installed and the
dev server is already running with live reload. **Build the user's app by editing `src/`.**

## Rules
- **Do not touch** `vite.config.ts`, `tsconfig*.json`, `package.json`, or `index.html` unless the
  user explicitly needs a new dependency. The config is known-good; regenerating it wastes turns
  and breaks the preview.
- Put the app UI in `src/App.tsx` (replace the placeholder). Split into components under
  `src/components/` as it grows.
- TypeScript everywhere. Keep components small and typed. Prefer plain React + CSS; add a library
  only if the task truly needs it.
- Match the example in `src/examples/` for structure, prop typing, and styling patterns.
- After editing, the preview reloads automatically — no build step to run.

## What exists
- `src/App.tsx` — entry component (currently a placeholder to replace).
- `src/components/` — put reusable components here.
- `src/examples/StatCard.tsx` — a golden example: a small, typed, styled component. Copy its shape.
