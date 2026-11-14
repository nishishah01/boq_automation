export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "Consolas", "monospace"],
      },
      colors: {
        brand: {
          50: "#F1F4FE", 100: "#E2E8FC", 200: "#C5D1F8", 300: "#9DB0F1", 400: "#6F86E6",
          500: "#4C63D9", 600: "#3A4FC4", 700: "#2F40A0", 800: "#29377F", 900: "#1E2757",
        },
        canvas: "#F6F7F9",
      },
      boxShadow: {
        card: "0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.06)",
        pop: "0 12px 32px -8px rgba(16,24,40,0.18), 0 4px 8px -4px rgba(16,24,40,0.08)",
      },
      keyframes: {
        "slide-in": { from: { transform: "translateX(100%)" }, to: { transform: "translateX(0)" } },
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "rise-in": { from: { opacity: "0", transform: "translateY(6px) scale(.98)" }, to: { opacity: "1", transform: "none" } },
      },
      animation: {
        "slide-in": "slide-in .22s cubic-bezier(.2,.8,.2,1)",
        "fade-in": "fade-in .15s ease-out",
        "rise-in": "rise-in .18s cubic-bezier(.2,.8,.2,1)",
      },
    },
  },
  plugins: [],
};
