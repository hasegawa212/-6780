module.exports = {
  apps: [
    {
      name: 'shibosei-bot',
      script: './app.js',
      instances: 1,
      exec_mode: 'fork',
      autorestart: true,
      watch: false,
      max_memory_restart: '300M',
      env: {
        NODE_ENV: 'production',
      },
      error_file: './logs/shibosei-error.log',
      out_file: './logs/shibosei-out.log',
      merge_logs: true,
      time: true,
    },
  ],
};
