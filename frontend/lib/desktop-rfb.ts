// A deliberately narrow RFB 3.8 client: true-color raw rectangles, no clipboard.
// Only the authenticated application bridge may supply this protocol stream.
export type RfbCallbacks = {
  send: (bytes: Uint8Array) => void;
  ready: (width: number, height: number) => void;
  rectangle: (x: number, y: number, width: number, height: number, rgba: Uint8ClampedArray) => void;
  frame: () => void;
};
const MAX_BYTES = 8 * 1024 * 1024;
export class DesktopRfb {
  private bytes = new Uint8Array(0);
  private phase = "version";
  private rectangles: number | null = null;
  private rectangle: { x: number; y: number; width: number; height: number } | null = null;
  width = 0;
  height = 0;
  private callbacks: RfbCallbacks;
  constructor(callbacks: RfbCallbacks) { this.callbacks = callbacks; }
  private consume(n: number) { this.bytes = this.bytes.slice(n); }
  push(chunk: Uint8Array) {
    if (this.bytes.length + chunk.length > MAX_BYTES) throw new Error("Desktop message is too large.");
    const joined = new Uint8Array(this.bytes.length + chunk.length);
    joined.set(this.bytes); joined.set(chunk, this.bytes.length); this.bytes = joined;
    while (this.parse()) { /* Parse only complete, bounded protocol records. */ }
  }
  private parse(): boolean {
    const b = this.bytes;
    const v = new DataView(b.buffer, b.byteOffset, b.byteLength);
    if (this.phase === "version") {
      if (b.length < 12) return false;
      if (new TextDecoder().decode(b.slice(0, 12)) !== "RFB 003.008\n") throw new Error("Unsupported desktop protocol.");
      this.consume(12); this.callbacks.send(new TextEncoder().encode("RFB 003.008\n")); this.phase = "security"; return true;
    }
    if (this.phase === "security") {
      if (!b.length) return false;
      if (b[0] < 1 || b[0] > 16) throw new Error("Unsupported desktop security.");
      if (b.length < b[0] + 1) return false;
      if (!b.slice(1, b[0]+1).includes(1)) throw new Error("Unsupported desktop security.");
      this.consume(b[0]+1); this.callbacks.send(new Uint8Array([1])); this.phase = "security-result"; return true;
    }
    if (this.phase === "security-result") {
      if (b.length < 4) return false;
      if (v.getUint32(0) !== 0) throw new Error("Desktop connection was refused.");
      this.consume(4); this.callbacks.send(new Uint8Array([1])); this.phase = "init"; return true;
    }
    if (this.phase === "init") {
      if (b.length < 24) return false;
      const width = v.getUint16(0), height = v.getUint16(2), nameLength = v.getUint32(20);
      if (width < 1 || width > 1280 || height < 1 || height > 800 || nameLength > 4096) throw new Error("Invalid desktop dimensions or name.");
      if (b.length < 24+nameLength) return false;
      this.width = width; this.height = height; this.consume(24+nameLength);
      // SetPixelFormat: 32 bpp, depth 24, little endian, true color RGB888.
      this.callbacks.send(new Uint8Array([0,0,0,0,32,24,0,1,0,255,0,255,0,255,16,8,0,0,0,0]));
      this.callbacks.send(new Uint8Array([2,0,0,1,0,0,0,0])); // raw encoding only
      this.callbacks.ready(width, height); this.phase = "messages"; this.request(false); return true;
    }
    if (this.rectangle) {
      const r = this.rectangle, size = r.width*r.height*4;
      if (b.length < size) return false;
      const rgba = new Uint8ClampedArray(size);
      for (let i=0; i<size; i+=4) { rgba[i]=b[i+2]; rgba[i+1]=b[i+1]; rgba[i+2]=b[i]; rgba[i+3]=255; }
      this.consume(size); this.callbacks.rectangle(r.x,r.y,r.width,r.height,rgba);
      this.rectangle = null; this.rectangles = (this.rectangles ?? 0)-1;
      if (this.rectangles === 0) { this.rectangles=null; this.callbacks.frame(); }
      return true;
    }
    if (this.rectangles !== null) {
      if (b.length < 12) return false;
      const x=v.getUint16(0), y=v.getUint16(2), width=v.getUint16(4), height=v.getUint16(6), encoding=v.getInt32(8);
      if (encoding !== 0 || width < 1 || height < 1 || x+width > this.width || y+height > this.height) throw new Error("Invalid or unsupported desktop rectangle.");
      this.consume(12); this.rectangle={x,y,width,height}; return true;
    }
    if (!b.length) return false;
    if (b[0] === 0) {
      if (b.length < 4) return false;
      const count = v.getUint16(2);
      if (count > 1024) throw new Error("Too many desktop rectangles.");
      this.consume(4); this.rectangles = count || null;
      if (!count) this.callbacks.frame();
      return true;
    }
    if (b[0] === 2) { this.consume(1); return true; } // bell
    if (b[0] === 3) {
      if (b.length < 8) return false;
      const size = v.getUint32(4);
      if (size > 65536) throw new Error("Desktop clipboard message is too large.");
      if (b.length < 8+size) return false;
      this.consume(8+size); return true; // explicitly discard clipboard
    }
    throw new Error("Unsupported desktop message.");
  }
  request(incremental=true) {
    if (!this.width) return;
    const b=new Uint8Array(10), v=new DataView(b.buffer); b[0]=3; b[1]=Number(incremental); v.setUint16(6,this.width); v.setUint16(8,this.height); this.callbacks.send(b);
  }
  pointer(x: number, y: number, buttons=0) {
    if (!this.width) return;
    const b=new Uint8Array(6), v=new DataView(b.buffer); b[0]=5; b[1]=buttons&255;
    v.setUint16(2,Math.max(0,Math.min(this.width-1,Math.round(x)))); v.setUint16(4,Math.max(0,Math.min(this.height-1,Math.round(y)))); this.callbacks.send(b);
  }
  key(keysym: number, down: boolean) {
    const b=new Uint8Array(8); b[0]=4; b[1]=Number(down); new DataView(b.buffer).setUint32(4,keysym); this.callbacks.send(b);
  }
  text(text: string) {
    for (const char of text.slice(0,2000)) { const code=char.codePointAt(0)!; const key=code<=255 ? code : 0x01000000+code; this.key(key,true); this.key(key,false); }
  }
}
export const DESKTOP_KEYS: Record<string, number> = { Backspace: 0xff08, Tab: 0xff09, Enter: 0xff0d, Escape: 0xff1b, Delete: 0xffff, Home: 0xff50, End: 0xff57, PageUp: 0xff55, PageDown: 0xff56, ArrowLeft: 0xff51, ArrowUp: 0xff52, ArrowRight: 0xff53, ArrowDown: 0xff54, Shift: 0xffe1, Control: 0xffe3, Alt: 0xffe9, Meta: 0xffeb };
