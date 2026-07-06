export function POST() {
  return Response.json({
    success: true,
    message: "Lark/Feishu connection setup completed.",
    status: {
      installed: true,
      version: "v1.0.65",
      manifest_version: "v1.0.65",
      app_configured: true,
      app_id: "cli_mock",
      app_brand: "feishu",
      skills_expected: 27,
      skills_installed: 4,
      installed_skills: ["lark-doc", "lark-im", "lark-shared", "lark-sheets"],
      enabled_skills: ["lark-doc", "lark-im", "lark-shared", "lark-sheets"],
      install_path: "/mock/users/default/skills/integrations/lark-cli",
      cli: {
        available: true,
        path: "/usr/bin/lark-cli",
        version: "lark-cli version v1.0.65",
        error: null,
      },
      auth: {
        status: "not_authorized",
        message: "Lark user authorization is not configured",
        user: null,
      },
    },
  });
}
