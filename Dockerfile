FROM node:20-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=ja_JP.UTF-8 \
    TZ=Asia/Tokyo

RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer \
      fonts-noto-cjk \
      fonts-ipafont-mincho \
      fonts-ipafont-gothic \
      locales \
      tzdata \
      ca-certificates \
    && sed -i 's/# *ja_JP.UTF-8 UTF-8/ja_JP.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package*.json ./
RUN npm ci --omit=dev

COPY . .

RUN mkdir -p output logs

CMD ["node", "app.js"]
