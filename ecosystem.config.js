module.exports = {
  apps: [
    {
      name: "kakao-talk-ai-bot",
      script: ".venv/bin/uvicorn",
      args: "main:app --host 0.0.0.0 --port 8000",
      cwd: __dirname,
      interpreter: "none",
      watch: false,
      autorestart: true,
      max_restarts: 10,
      env: {
        NODE_ENV: "production",
      },
    },
  ],
};
