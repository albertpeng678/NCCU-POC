// tests/frontend/strip-table.test.mjs
import { strict as assert } from "node:assert";
import test from "node:test";
import { stripInProgressTable } from "../../frontend/progressive-md.js";

test("純 prose 原樣回傳", () => {
  assert.equal(stripInProgressTable("這是一段文字"), "這是一段文字");
});

test("進行中表格(未收尾)→ 砍到表格列前", () => {
  const tail = "以下是課程：\n| 課程名稱 | 系所 |";
  assert.equal(stripInProgressTable(tail), "以下是課程：\n");
});

test("已收尾(空行)表格 → 原樣回傳交給 commit", () => {
  const tail = "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n";
  assert.equal(stripInProgressTable(tail), tail);
});

test("空字串安全", () => {
  assert.equal(stripInProgressTable(""), "");
});
