# Frontend 靜態服務（Railway frontend service 用）
# 將 frontend/ 的 HTML/CSS/JS 以 nginx 提供，並監聽 Railway 注入的 $PORT。
FROM nginx:1.27-alpine

# 靜態檔
COPY index.html style.css app.js careers.js /usr/share/nginx/html/

# nginx 設定模板：用 $PORT 取代埠（Railway 在 runtime 注入 PORT）
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# 預設 PORT（本地 docker run 時用；Railway 會覆蓋）
ENV PORT=8080
EXPOSE 8080

# nginx:alpine 內建 envsubst 模板機制（/etc/nginx/templates/*.template → /etc/nginx/conf.d/）
CMD ["nginx", "-g", "daemon off;"]
