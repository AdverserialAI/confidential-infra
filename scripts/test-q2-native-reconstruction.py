#!/usr/bin/env python3
"""Artifact integrity/atomic payload tests with tiny synthetic files, no cloud."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from gguf import GGUFWriter, GGMLQuantizationType

spec=importlib.util.spec_from_file_location('reconstruction',Path(__file__).with_name('reconstruct-q2-native.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


def write_native(path, entries):
    header={};offset=0;payload=b''
    for name,dtype,shape,raw in entries:
        header[name]={'dtype':dtype,'shape':shape,'data_offsets':[offset,offset+len(raw)]}
        offset+=len(raw);payload+=raw
    raw=json.dumps(header,separators=(',',':')).encode()
    raw+=b' '*((-len(raw))%8)
    path.write_bytes(struct.pack('<Q',len(raw))+raw+payload)
    return header


class ReconstructionTests(unittest.TestCase):
    def test_source_tree_identity_detects_mutation_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);p=root/'model.gguf';p.write_bytes(b'measured model')
            sha=hashlib.sha256(p.read_bytes()).hexdigest();tree=hashlib.sha256(f'model.gguf\0{p.stat().st_size}\0{sha}\n'.encode()).hexdigest()
            digest='sha256:'+tree
            manifest={'version':1,'algorithm':'sha256-tree-v1','model_id':'adverserial/cyberseek','digest':digest,'files':[{'path':'model.gguf','size':p.stat().st_size,'sha256':sha}]}
            self.assertEqual(r.source_identity(root,manifest,digest),digest)
            p.write_bytes(b'altered model')
            with self.assertRaises(ValueError):r.source_identity(root,manifest,digest)
            p.unlink();p.symlink_to('/etc/hosts')
            with self.assertRaises(ValueError):r.source_identity(root,manifest,digest)

    def test_native_replacement_preserves_unmodified_payload_and_header(self):
        class Source:
            def native(self, projection, expert):
                self.expert=expert
                return np.full((32,32),0xAB,dtype=np.uint8),np.full((32,2),127,dtype=np.uint8),{'relative_rms_error':.01}
        weight='layers.11.ffn.experts.0.w2.weight';scale='layers.11.ffn.experts.0.w2.scale'
        output={'weight':weight,'scale':scale,'weight_dtype':'I8','scale_dtype':'F8_E8M0','weight_shape':[32,32],'scale_shape':[32,2]}
        projection={'gguf_tensor':'blk.11.ffn_down_exps.weight','native_outputs':[output]}
        plan={'projections':[projection],'native_replacement_tensor_and_scale_count':2}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'model.safetensors.partial'
            original=write_native(path,[(scale,'F8_E8M0',[32,2],b'\0'*64),('untouched','F32',[2],struct.pack('<ff',1.25,9.5)),(weight,'I8',[32,32],b'\0'*1024)])
            source=Source();result=r.patch_shard(path,source,r.replacement_map(plan),{weight:'model.safetensors',scale:'model.safetensors','untouched':'model.safetensors'})
            header,offset=r.safetensors_header(path);self.assertEqual(header,original)
            data=path.read_bytes()[offset:]
            self.assertEqual(data[:64],bytes([127])*64)
            self.assertEqual(data[64:72],struct.pack('<ff',1.25,9.5))
            self.assertEqual(data[72:],bytes([0xAB])*1024)
            self.assertEqual(set(result['replaced_tensors']),{weight,scale})
            self.assertEqual(source.expert,0)
            with self.assertRaises(ValueError):r.patch_shard(path,source,r.replacement_map(plan),{weight:'model.safetensors',scale:'model.safetensors'})

    def test_real_gguf_reader_raw_slice_and_lossless_native_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'part.gguf'
            data=np.zeros((1,32,34),dtype=np.uint8);data[:,:,0::17]=127;data[:,:,1]=0x21
            writer=GGUFWriter(path,'deepseek41');writer.add_tensor('blk.11.ffn_down_exps.weight',data,raw_dtype=GGMLQuantizationType.MXFP4)
            writer.write_header_to_file();writer.write_kv_data_to_file();writer.write_tensors_to_file(progress=False);writer.close()
            projection={'gguf_tensor':'blk.11.ffn_down_exps.weight','logical_shape':[1,32,64],'ggml_type_id':39}
            source=r.PatchedSource(Path(directory),{'projections':[projection]})
            weight,scale,metrics=source.native(projection,0)
            self.assertEqual(weight.shape,(32,32));self.assertEqual(scale.shape,(32,2))
            self.assertEqual(metrics['relative_rms_error'],0.)

    def test_auxiliary_download_is_integrity_checked_before_acceptance(self):
        body=b'immutable metadata'
        sha=hashlib.sha256(body).hexdigest()
        class Response:
            status=200
            def __enter__(self):self.offset=0;return self
            def __exit__(self,*args):pass
            def read(self,size):part=body[self.offset:self.offset+size];self.offset+=len(part);return part
        with tempfile.TemporaryDirectory() as directory,patch.object(r.urllib.request,'urlopen',side_effect=lambda *a,**k:Response()),patch.object(r.time,'sleep'):
            path=Path(directory)/'download.partial'
            self.assertEqual(r.download_native('config.json',{'size':len(body),'sha256':sha},path),sha)
            with self.assertRaises(ValueError):r.download_native('config.json',{'size':len(body),'sha256':'0'*64},path)
            with self.assertRaises(ValueError):r.download_native('config.json',{'size':len(body)-1,'sha256':sha},path)

    def test_atomic_json_and_compact_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json';r.atomic_json(path,{'a':3});self.assertEqual(json.loads(path.read_text()),{'a':3});self.assertFalse(path.with_name('state.json.tmp').exists())
        item={'gguf_tensor':'blk.11.ffn_down_exps.weight','logical_shape':[384,32,64],'native_output_template':{'weight':'layers.11.ffn.experts.{expert}.w2.weight','scale':'layers.11.ffn.experts.{expert}.w2.scale'}}
        result=r.expand_plan({'projections':[item]})
        self.assertEqual(len(result['projections'][0]['native_outputs']),384)
        self.assertEqual(result['projections'][0]['native_outputs'][383]['weight'],'layers.11.ffn.experts.383.w2.weight')


if __name__=='__main__':unittest.main()
