/** Execute actual App function/callback source: no copied logout implementation. */
// @ts-expect-error Node builtin available in Vitest, not browser dependency.
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { expect, it, vi } from 'vitest';
const source=readFileSync('src/App.tsx','utf8');
const ast=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
function find(predicate: (n: ts.Node)=>boolean) {
 const nodes: ts.Node[]=[];function visit(n:ts.Node){if(predicate(n))nodes.push(n);ts.forEachChild(n,visit);}visit(ast);
 if(nodes.length!==1)throw Error('Ambiguous actual App callback');return nodes[0].getText(ast);
}
const logout=find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='logout');
const bootstrap=find(n=>ts.isCallExpression(n)&&n.expression.getText(ast)==='useEffect'&&n.arguments[0]?.getText(ast).includes('api("/auth/me")')===true);
function compiled(code:string){return ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;}
it('confirmed offline logout immediately closes UI, warns about preserving pending photos, never waits for push',async()=>{
 const calls:string[]=[];const confirm=vi.fn((_message:string)=>true),seq={current:0};let release!:()=>void;
 const cache=new Promise<void>(resolve=>{release=resolve;});
 const dependencies={window:{confirm},revokePushDevice:()=>new Promise(()=>{}),beginLocalSignout:()=>{calls.push('fence');return[];},loadSequenceRef:seq,setCurrentUser:()=>calls.push('user-null'),setReady:(v:boolean)=>calls.push('ready:'+v),setSignoutNotice:vi.fn(),clearOwnedBrowserCaches:()=>cache,flushPendingRevocations:async()=>{throw Error('SYNTHETIC offline');},isLocallySignedOut:()=>true};
 const fn=new Function(...Object.keys(dependencies),compiled(logout)+';return logout;')(...Object.values(dependencies));
 const pending=fn();expect(calls).toEqual(['fence','user-null','ready:false']);expect(seq.current).toBe(1);
 expect(confirm.mock.calls[0][0]).toContain('Неотправленные фото и правки будут сохранены');release();await pending;
});
it('cancel warning leaves session and all local data unchanged',async()=>{
 const fence=vi.fn();const fn=new Function('window','beginLocalSignout',compiled(logout)+';return logout;')({confirm:()=>false},fence);
 await fn();expect(fence).not.toHaveBeenCalled();
});
it('actual bootstrap never calls auth/me while durable local signout is pending',()=>{
 const request=vi.fn(),ready=vi.fn();let effect!:()=>void;
 new Function('useEffect','isLocallySignedOut','signoutGeneration','setReady','api',compiled(bootstrap))((callback:()=>void)=>{effect=callback;},()=>true,()=>1,ready,request);
 effect();expect(request).not.toHaveBeenCalled();expect(ready).toHaveBeenCalledWith(false);
});
