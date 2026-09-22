# Design System (portable)

Sistema visual genérico y reutilizable en otros proyectos: tokens neutros,
componentes base y reglas de layout. Sin duplicados ni código muerto.
Los valores de este documento forman el **tema `mendi`** (apéndice);
para otro proyecto basta redefinir los tokens.

Convenciones del documento: `var(--x)` = token a definir una vez;
`@theme` = bloque `html[data-theme="light"]` que sobrescribe valores.

---

## 1. Uso en otro proyecto

1. Crear `tokens.css` con §2 + `@theme` con §3 y cargarlo **antes** que
   `components.css`.
2. Conmutar tema con `document.documentElement.dataset.theme =
   "dark" | "light"` (persistir en `localStorage`, default `dark`).
3. Fuentes: autohospedar woff2 con `font-display: swap` + `preload` de
   los pesos 400 (ver §4).
4. No usar CDN para CSS/JS crítico del layout.

## 2. Tokens genéricos

### 2.1 Superficies y texto

| Token | Rol | Tema mendi dark | Tema mendi light |
|---|---|---|---|
| `--color-bg` | fondo app | `#0A0E14` | `#FAFAF7` |
| `--color-bg-2` | sidebar, footers | `#0D121A` | `#F4F2EA` |
| `--color-surface` | cards, inputs | `#10151E` | `#FFFFFF` |
| `--color-surface-2` | hover, thead, controles | `#161C28` | `#F5F2E9` |
| `--color-surface-3` | tooltips, popups | `#1C2433` | `#ECE8DC` |
| `--color-border` | bordes universales | `#1F2937` | `#E5E1D2` |
| `--color-border-2` | bordes interactivos | `#2A3441` | `#D2CCB8` |
| `--color-text` | texto principal | `#F2EDE2` | `#1B2230` |
| `--color-text-muted` | secundario | `#9BA1AB` | `#5A6371` |
| `--color-text-dim` | labels mono 10–11px | `#5A6371` | `#8E94A0` |

### 2.2 Acentos y niveles semánticos

| Token | Rol | dark | light |
|---|---|---|---|
| `--color-primary` | acción, foco, activo | `#B5D17A` | `#6E8847` |
| `--color-primary-deep` | hover primario, degradados | `#87A559` | `#4F6630` |
| `--color-warm` | aviso / nivel hard | `#E8B86D` | `#B8853F` |
| `--color-cool` | info / nivel easy | `#7DAFC9` | `#4E83A0` |
| `--color-danger` | error / nivel very-hard | `#E47862` | `#C75A44` |
| `--color-easy` | alias de cool | `var(--color-cool)` | `var(--color-cool)` |
| `--color-moderate` | alias de primary | `var(--color-primary)` | `var(--color-primary)` |
| `--color-hard` | alias de warm | `var(--color-warm)` | `var(--color-warm)` |
| `--color-very-hard` | alias de danger | `var(--color-danger)` | `var(--color-danger)` |

Fondos y bordes translúcidos por nivel (`--tag-{easy,mod,hard,vh}-{bg,border}`).
Tema mendi dark: `rgba(125,175,201,.07)/.3`, `rgba(181,209,122,.07)/.3`,
`rgba(232,184,109,.07)/.3`, `rgba(228,120,98,.07)/.3`;
light: `rgba(78,131,160,.08)/.4`, `rgba(110,136,71,.08)/.4`,
`rgba(184,133,63,.1)/.4`, `rgba(199,90,68,.08)/.4`.

### 2.3 Tipografía, radios, sombras, foco

```css
--font-sans: 'IBM Plex Sans', system-ui, sans-serif;
--font-mono: 'IBM Plex Mono', ui-monospace, monospace;
--font-serif: 'Fraunces', Georgia, serif;

--radius-sm: 8px;    /* inputs, chips pequeños */
--radius-md: 12px;   /* cards, modales */
--radius-lg: 14px;   /* cards grandes */
--radius-pill: 999px;

--space-1: 4px; --space-2: 8px; --space-3: 12px;
--space-4: 18px; --space-5: 24px; --space-6: 40px;

--shadow-card: 0 12px 40px -16px rgba(0,0,0,.5);   /* + versión light suave */
--overlay-bg: rgba(0,0,0,.55);
--focus-ring: 0 0 0 3px rgba(181,209,122,.12);
```

Escala: serif display 22–92px (300–500), sans cuerpo 12–14px (300–600),
mono etiquetas 9–12px uppercase + `letter-spacing`. Números tabulares con
`font-variant-numeric: tabular-nums`.

## 3. Layout y responsive

- Shell: grid `220px 1fr` (sidebar sticky + contenido); sidebar a drawer
  bajo el breakpoint medio; `main` en flex-column con `min-height: 100vh`.
- **Reglas duras**: todo hijo directo de grid lleva `min-width: 0`;
  `overflow-x: clip` (nunca `hidden`) en el contenedor principal para no
  romper `position: sticky`; scroll horizontal solo en contenedores
  dedicados (`overflow-x: auto` explícito).
- Breakpoints: `480px` (móvil: 1 columna, drawer, tablas con scroll),
  `768px` (tablet: 2 columnas, grids apilados), `1024px` (desktop estrecho:
  3 columnas, densidades reducidas). Excepción mendi: el drawer persiste
  en `900px` por el ancho del sidebar.
- Secciones: `.section` con padding `40px` (20px en móvil), cabecera
  flex `space-between` que colapsa a columna.

## 4. Componentes base

Un canónico por patrón (markup mínimo entre paréntesis):

- **Botones**: `.btn` (primary, h 36px, mono uppercase) /
  `.btn-ghost` (transparente + borde) / `.btn-icon` (32px, solo icono).
  Hover: `brightness(1.08)` / `surface-2`. Focus: `--focus-ring`.
- **Card**: `surface + border + radius-md/lg`, padding 18–24px, con
  variantes de cabecera (`eyebrow` mono 10px + `h3` serif 18px).
- **Tag / chip / dot**: un solo sistema por nivel
  (`.tag-{easy,moderate,hard,very-hard}` + `.dot[data-level]` 8px);
  contadores en pill mono.
- **Tabla**: `thead` mono 10.5px uppercase sobre `surface-2`, filas 13px
  con hover, footer de acciones; `min-width: 640px` + scroll dedicado
  en móvil.
- **Formularios**: input/select/textarea 32–38px sobre `surface` con
  `border-2`; focus `border: primary` + `--focus-ring`; `label` mono
  10px uppercase; combobox y date-range como composición de lo anterior.
- **Modal**: backdrop blur (`--overlay-bg`) + card 420–540px con
  head/body/actions; inputs con hint y estado de error.
- **Overlay/tooltip**: misma superficie y radio que modal en pequeño;
  `pointer-events: none` en tooltips de gráfico.
- **Feedback**: `.flash` ok/err, `.empty` centrado con CTA, skeleton
  shimmer, spinner `border-top` primario, toasts apilables.
- **Navegación**: sidebar (`brand`, `nav-item` con `.active` en
  `primary`, sección, footer) + topbar sticky con blur (`breadcrumb`,
  acciones, toggle de tema) + tabs con `border-bottom` activa +
  sub-nav sticky con scroll-x intencional.
- **Paginación/infinito**: sentinel + skeletons de fila.

## 5. Iconografía

SVG inline `stroke="currentColor"`, grosor único **1.7**, tamaños
13–16px (acciones) y 10–11px (meta). Un solo `favicon.ico` referenciado
desde el layout base.

## 6. Lo excluido a propósito (extensión de dominio)

Mapas Leaflet y su theming, charts SVG (donuts, scatter, sparklines),
calendarios heatmap, comparadores superpuestos, `.hero-tag`, `.pill`,
role-chips con paleta propia y vistas con datos vivos. Cada uno vive en
su módulo y reutiliza §2–§4 sin añadir tokens globales.

## 7. Tema `mendi` (apéndice)

Valores de las tablas §2 + `Fraunces`/`IBM Plex` autohospedadas
(Plex Mono sin peso 600) + drawer en `900px` + paleta cálida/nocturna.
Para retemar: sobrescribir solo §2 y el `@theme`; los componentes no
contienen ningún color literal.
