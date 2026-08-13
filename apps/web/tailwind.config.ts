import type { Config } from "tailwindcss";

/**
 * Colours are declared once as CSS custom properties in globals.css and referenced here,
 * so a token cannot drift between the stylesheet and the utility classes.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: "hsl(var(--color-bg) / <alpha-value>)",
        surface: "hsl(var(--color-surface) / <alpha-value>)",
        "surface-raised": "hsl(var(--color-surface-raised) / <alpha-value>)",
        border: "hsl(var(--color-border) / <alpha-value>)",
        "border-strong": "hsl(var(--color-border-strong) / <alpha-value>)",
        text: "hsl(var(--color-text) / <alpha-value>)",
        "text-muted": "hsl(var(--color-text-muted) / <alpha-value>)",
        "text-subtle": "hsl(var(--color-text-subtle) / <alpha-value>)",
        accent: "hsl(var(--color-accent) / <alpha-value>)",
        "accent-hover": "hsl(var(--color-accent-hover) / <alpha-value>)",
        "accent-subtle": "hsl(var(--color-accent-subtle) / <alpha-value>)",
        danger: "hsl(var(--color-danger) / <alpha-value>)",
        "danger-subtle": "hsl(var(--color-danger-subtle) / <alpha-value>)",
        warning: "hsl(var(--color-warning) / <alpha-value>)",
        "warning-subtle": "hsl(var(--color-warning-subtle) / <alpha-value>)",
        success: "hsl(var(--color-success) / <alpha-value>)",
        "success-subtle": "hsl(var(--color-success-subtle) / <alpha-value>)",
        ai: "hsl(var(--color-ai) / <alpha-value>)",
        "ai-subtle": "hsl(var(--color-ai-subtle) / <alpha-value>)",
      },
      borderRadius: { DEFAULT: "var(--radius)" },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
