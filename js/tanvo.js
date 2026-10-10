import { app } from "../../scripts/app.js";

// Settings → Tanvo → API key. The nodes read it from ComfyUI's saved settings when they run;
// TANVO_API_KEY in the environment takes precedence. Without a key they use Tanvo's free tier.
app.registerExtension({
  name: "Tanvo.Settings",
  settings: [
    {
      id: "Tanvo.APIKey",
      category: ["Tanvo", "Account", "API key"],
      name: "Tanvo API key",
      tooltip: "Create one at tanvo.ai/settings/apikeys. Leave empty for the free tier (free model only, watermarked).",
      type: "text",
      defaultValue: "",
      attrs: { type: "password", placeholder: "sk_…", autocomplete: "off" },
    },
  ],
});
