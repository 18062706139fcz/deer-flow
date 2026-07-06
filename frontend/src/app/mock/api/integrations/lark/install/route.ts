const installedSkills = ["lark-doc", "lark-im", "lark-shared", "lark-sheets"];

export function POST() {
  return Response.json({
    success: true,
    installed_skills: installedSkills,
    message: `Installed ${installedSkills.length} Lark/Feishu skills.`,
    status: {
      installed: true,
      version: "v1.0.65",
      manifest_version: "v1.0.65",
      app_configured: false,
      app_id: null,
      app_brand: null,
      skills_expected: 27,
      skills_installed: installedSkills.length,
      installed_skills: installedSkills,
      enabled_skills: installedSkills,
      install_path: "/mock/users/default/skills/integrations/lark-cli",
      cli: {
        available: true,
        path: "/usr/bin/lark-cli",
        version: "lark-cli version v1.0.65",
        error: null,
      },
      auth: {
        status: "not_configured",
        message: "lark-cli auth is not configured",
        user: null,
      },
    },
  });
}
