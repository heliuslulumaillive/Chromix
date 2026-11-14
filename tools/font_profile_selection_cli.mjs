// Selection-only candidate entry. No browser, user profile or formal kernel
// migration occurs here. A launch may use only the admitted saved configuration.
import {readFile} from 'node:fs/promises';
import {fixedSelection,verifiedPool} from './font_installation_profiles.mjs';
const args={};for(let i=2;i<process.argv.length;i+=2){if(!process.argv[i]?.startsWith('--')||process.argv[i+1]===undefined)throw Error('Expected --key value');args[process.argv[i].slice(2)]=process.argv[i+1];}
const registry=JSON.parse(await readFile(args.registry,'utf8')),host=JSON.parse(await readFile(args.host,'utf8'));
const options={country:args.country,evidenceRoot:args.evidence,expectedDLLSHA256:args.dll,hostEnvironment:host.environment,hostInventorySHA256:host.fontInventorySHA256};
if(args.action==='list')console.log(JSON.stringify(await verifiedPool(registry,options),null,2));
else if(args.action==='select')console.log(JSON.stringify(await fixedSelection({...options,registry,directory:args.directory,seed:args.seed}),null,2));
else throw Error('Use --action list or select; country must be explicit JP/KR/US');
