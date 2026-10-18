// Screen state: one object with every field declared, the API client bound to its page token, and graph lookups.
import {createClient} from './http.js';
export const state = {token:null,role:null,team:false,snapshots:[],snapshot:null,snapshotId:null,review:null,view:'overview',vpc:null,focus:null,references:false,routes:null,revision:0,xml:null,history:[],visible:null,
  ready:false,loading:true,dirty:false,busy:false,scrollY:0,mode:'diagram',selected:null,identity:null,sso:null,ssoDefaults:null,ssoTimer:null,chatReady:undefined,chatBusy:false,
  standards:[],presets:[],xmlBusy:false};
export const {api, request, streamApi} = createClient(() => state.token);
export const node = id => state.snapshot.graph.nodes.find(n=>n.id===id);
export const vpcNode = resourceId => state.snapshot.graph.nodes.find(n=>n.kind==='vpc'&&n.resource_id===resourceId);
export function relationsOf(id) {return state.snapshot.graph.edges.filter(e=>(e.source===id||e.target===id)&&e.relation!=='in_vpc');}
export function vpcOf(id) {const edge=state.snapshot.graph.edges.find(e=>e.source===id&&e.relation==='in_vpc');return edge?node(edge.target)?.resource_id:null;}
