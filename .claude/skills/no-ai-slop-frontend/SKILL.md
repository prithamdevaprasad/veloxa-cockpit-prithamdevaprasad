---
name: no-ai-slop-frontend
description: Enforces distinctive, polished, coherent frontend design for Veloxa. Use whenever designing, implementing, reviewing or improving React pages and components.
---

# No AI slop: Veloxa frontend standards

Veloxa is an evidence-grounded intelligence workspace for a CEO. Every screen must look art-directed and
trustworthy, not generated from a template. Use this skill together with `frontend-design` whenever you design,
build, review or change a page or component in `frontend/`.

## Do not use

- Generic AI-chatbot layouts, glowing gradient circles or "AI orbs", sparkles, or robot illustrations.
- Large purple or blue gradients, gradient text, or glassmorphism on every surface (the purple header is flat).
- Corner radii chosen at random. Radii follow the scale in `src/workspace.css`: controls 8px, cards 12px, the Ask
  panel 16px, and pills only for status labels.
- Pill-shaped elements where a plain button or link does the job.
- Huge empty hero sections, marketing copy ("Unlock insights!"), or slogans inside the application.
- Generic dashboard templates, or the same bento grid repeated on every page.
- Stock photographs, random Unsplash images, or mixed illustration styles. Avatars and department art come only from
  `src/ui/art.tsx` (one family).
- Emoji used as icons.
- Decorative charts with no data, and charts where a sentence is clearer.
- Heavy shadows, glows, or continuous animation (pulsing, shimmering decorations, parallax).
- Fake metrics, invented trends, invented percentages or invented activity. Every number is rendered from the API
  and says what it was computed from.
- Tracked-out ALL-CAPS eyebrow labels above headings, or a "→" appended to button text.

## Required

- **Hierarchy:** one page title, then section titles, then the content. The most important number or sentence is
  the largest thing in its region.
- **Typography:** IBM Plex Sans for the interface and IBM Plex Mono only for identifiers (record IDs, batch K,
  hashes). Use tabular numerals for every metric. Keep line length under about 75 characters for prose.
- **Colour (owner direction, 18 Nov 2026, inspired by Keka HRMS):** a deep-purple header and dark-purple icon
  sidebar around light surfaces (very light grey page, white cards, thin borders, subtle shadows). Purple
  marks interaction and selection only. Green, amber and red carry status meaning only. Do not copy Keka's
  layout, branding or data; adapt the visual language.
- **Spacing:** 4px scale (4, 8, 12, 16, 24, 32). Cards in one grid align to the same gutter.
- **Responsive behaviour:** the layout reorganises on narrow screens; it isn't just shrunk. Tables scroll inside
  their card; the page never scrolls sideways. Ask is full-screen on phones.
- **States:** every data region has loading (skeleton), empty (what to do next), error (what went wrong and how to
  fix it), permission-denied and refused states.
- **Accessibility:** semantic elements, visible focus rings, full keyboard operation, sufficient contrast (WCAG AA),
  and `prefers-reduced-motion` respected.
- **Motion:** 160–220ms with ease-out, only in response to a user action (open, expand, select, confirm) or to
  reveal data once. Nothing loops.
- **Copy:** plain English from the user's point of view, in sentence case. Buttons say what happens. Errors say what
  happened and what to do. Empty states invite the next action.
- **Honesty:** never make an answer sound more certain than its evidence. Show partial, conflicting, refused and
  "not enough evidence" states clearly. Synthetic data always carries a visible DEMO label.

## Review checklist before finishing

1. Take screenshots at 1440, 1280, tablet and phone widths and look at them.
2. Look for: dead horizontal space, clipping, misalignment, inconsistent radii or borders, low contrast, a page that
   looks different from the rest, and anything decorative that carries no information.
3. Remove one accessory: cut the least useful decoration on each page.
4. Confirm that no number on screen is missing its basis or source.
