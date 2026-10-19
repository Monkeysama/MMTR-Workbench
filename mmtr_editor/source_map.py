"""Absolute IR line identities for one validated entry; no textual guessing."""
import re


class IRSourceIndex:
    def __init__(self,ir):
        self.positions={};self.definitions={};self.labels={};self.terms={}
        inside=False;block='0';position=0
        for number,raw in enumerate(ir.splitlines(),1):
            if raw.startswith('define '):
                inside=True;self.labels['0']=number
                continue
            if inside and raw.startswith('}'):break
            if not inside:continue
            label=re.match(r'(?:; <label>:(\d+)|([\w.]+):)',raw.strip())
            if label:
                block=label[1] or label[2];position=0;self.labels[block]=number
                continue
            line=raw.split(';',1)[0].strip()
            if not line:continue
            self.positions[(block,position)]=number;position+=1
            definition=re.match(r'(%[\w.]+) = ',line)
            if definition:self.definitions[definition[1]]=number
            if line=='ret void' or line.startswith('br '):self.terms[block]=number


def expand_source_map(lines,origins):
    """Expand generator items that contain newlines into physical HLSL rows."""
    return [sorted(set(origins.get(index,()))) for index,text in enumerate(lines) for _ in text.split('\n')]
