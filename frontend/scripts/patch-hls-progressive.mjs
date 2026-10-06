import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import ts from 'typescript';

const marker = 'treasure-progressive-timeline-v1';
const hash = (value) => createHash('sha256').update(value).digest('hex');
const packageRoot = new URL('../node_modules/hls.js/', import.meta.url);

function walk(node, visit) {
  visit(node);
  ts.forEachChild(node, (child) => walk(child, visit));
}

function methodName(node) {
  if (ts.isMethodDeclaration(node)) return node.name.getText();
  if (
    ts.isFunctionExpression(node) &&
    ts.isBinaryExpression(node.parent) &&
    ts.isPropertyAccessExpression(node.parent.left)
  )
    return node.parent.left.name.text;
  return undefined;
}

// Parse the official TS, ESM, UMD and standalone worker with the same AST edit.
// Input and output hashes are pinned separately; this never guesses at an upgrade.
export function patchHlsSource(source, file) {
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  const edits = [];
  const matches = { remux: 0, resetTimeStamp: 0, resetNextTimestamp: 0, resetInitSegment: 0 };
  walk(tree, (node) => {
    const name = methodName(node);
    if (!name || !node.body) return;
    const body = node.body.getText(tree);
    const insertReset =
      (name === 'resetTimeStamp' &&
        body.includes('this.lastEndTime') &&
        body.includes('this.initPTS')) ||
      (name === 'resetNextTimestamp' &&
        body.includes('this.lastEndTime') &&
        body.includes('this.isVideoContiguous')) ||
      (name === 'resetInitSegment' &&
        body.includes('this.pendingInitSegment') &&
        body.includes('this.videoOnlyRemux'));
    if (insertReset) {
      matches[name]++;
      edits.push({
        start: node.body.getStart(tree) + 1,
        end: node.body.getStart(tree) + 1,
        text: '\nthis.__treasureProgressiveKey = undefined; this.__treasureProgressiveChunk = -1;\n',
      });
    }
    if (
      name !== 'remux' ||
      !body.includes('remapping timestamps (initPTS)') ||
      !body.includes('this.lastEndTime')
    )
      return;
    let initName;
    walk(node.body, (child) => {
      if (!ts.isVariableDeclaration(child) || !child.initializer) return;
      if (child.initializer.getText(tree) === 'this.initPTS' && ts.isIdentifier(child.name))
        initName = child.name.text;
      if (child.initializer.getText(tree) === 'this' && ts.isObjectBindingPattern(child.name)) {
        for (const element of child.name.elements) {
          if ((element.propertyName || element.name).getText(tree) === 'initPTS')
            initName = element.name.getText(tree);
        }
      }
    });
    if (!initName || node.parameters.length < 9)
      throw new Error(`Unrecognized remux arguments: ${file}`);
    const playlist = node.parameters[7].name.getText(tree);
    const chunk = node.parameters[8].name.getText(tree);
    walk(node.body, (child) => {
      if (
        !ts.isIfStatement(child) ||
        !child.expression.getText(tree).includes(`${initName}.timescale`) ||
        !child.thenStatement.getText(tree).includes('remapping timestamps (initPTS)')
      )
        return;
      matches.remux++;
      edits.push({
        start: child.getStart(tree),
        end: child.getStart(tree),
        text:
          `/* ${marker} */\n` +
          `var __treasureProgressiveKey = [${playlist}, ${chunk}.level, ${chunk}.sn, ${chunk}.part].join(':');\n` +
          `var __treasureProgressiveSame = !${chunk}.iframe && this.__treasureProgressiveKey === __treasureProgressiveKey && ${chunk}.id >= this.__treasureProgressiveChunk;\n` +
          `this.__treasureProgressiveKey = __treasureProgressiveKey; this.__treasureProgressiveChunk = ${chunk}.id;\n`,
      });
      edits.push({
        start: child.expression.getStart(tree),
        end: child.expression.end,
        text: `(${child.expression.getText(tree)}) && (!${initName} || !__treasureProgressiveSame)`,
      });
    });
  });
  for (const [name, count] of Object.entries(matches)) {
    if (count !== 1) throw new Error(`Expected one ${name}, found ${count}: ${file}`);
  }
  if (file.endsWith('.ts')) {
    let declarations = 0;
    walk(tree, (node) => {
      if (ts.isClassDeclaration(node) && node.name?.text === 'PassThroughRemuxer') {
        const start = node.members.pos;
        edits.push({
          start,
          end: start,
          text: '\nprivate __treasureProgressiveKey?: string;\nprivate __treasureProgressiveChunk = -1;\n',
        });
        declarations++;
      }
    });
    if (declarations !== 1) throw new Error(`Missing source class: ${file}`);
  }
  let output = source;
  for (const edit of edits.sort((a, b) => b.start - a.start)) {
    output = output.slice(0, edit.start) + edit.text + output.slice(edit.end);
  }
  const parsed = ts.createSourceFile(file, output, ts.ScriptTarget.Latest, true);
  if (parsed.parseDiagnostics.length) throw new Error(`Patch produced invalid syntax: ${file}`);
  return output;
}

export function applyPatch(check = false) {
  const manifest = JSON.parse(
    readFileSync(new URL('./hls-progressive-manifest.json', import.meta.url), 'utf8'),
  );
  const upstream = JSON.parse(readFileSync(new URL('package.json', packageRoot), 'utf8'));
  if (upstream.version !== manifest.version)
    throw new Error(`Review the progressive patch before upgrading hls.js ${upstream.version}`);
  const pending = [];
  for (const [file, expected] of Object.entries(manifest.files)) {
    const url = new URL(file, packageRoot);
    const original = readFileSync(url, 'utf8');
    const digest = hash(original);
    if (digest === expected.patched) continue;
    if (digest !== expected.original) throw new Error(`Unexpected hls.js file content: ${file}`);
    if (check) throw new Error(`Progressive patch missing: ${file}; run npm run postinstall`);
    const patched = patchHlsSource(original, file);
    if (hash(patched) !== expected.patched)
      throw new Error(`Progressive patch output mismatch: ${file}`);
    pending.push([url, patched]);
  }
  for (const [url, content] of pending) writeFileSync(url, content);
  console.log(
    `hls.js ${upstream.version}: progressive timeline patch ${check ? 'verified' : 'ready'} (${manifest.patch})`,
  );
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  applyPatch(process.argv.includes('--check'));
}
