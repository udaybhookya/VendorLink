import { useEffect } from "react";

/** Which installable app this page is: the vendor app or the retailer console. Each has its own
 * manifest (name, icon, start page), so "Install app" in the browser installs the right one. */
type App = "vendor" | "console";
const THEME: Record<App, string> = { vendor: "#0f766e", console: "#1e293b" };
const TITLE: Record<App, string> = { vendor: "VendorLink", console: "VendorLink Console" };

export function useApp(app: App): void {
  useEffect(() => {
    document.getElementById("manifest")?.setAttribute("href", `/${app}.webmanifest`);
    document.getElementById("favicon")?.setAttribute("href", `/icons/${app}.svg`);
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME[app]);
    document.title = TITLE[app];
  }, [app]);
}
