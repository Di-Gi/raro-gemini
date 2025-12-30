FROM node:20-alpine as builder

WORKDIR /app

COPY package.json .
RUN npm install

COPY . .
RUN npm run build

FROM node:20-alpine

WORKDIR /app

COPY --from=builder /app/dist ./dist
COPY package.json .

# FIX: Removed '--production' so that 'vite' (a devDependency) is installed
RUN npm install

EXPOSE 5173

# 'npm run preview' runs 'vite preview', which now exists
CMD ["npm", "run", "preview"]