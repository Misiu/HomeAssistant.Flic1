// Ghidra headless post-script: dump disassembly of given functions.
// Args: <out.txt> <entryAddrHex> [<entryAddrHex> ...]  (addresses as shown by Ghidra, e.g. 00108e0a)
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;

public class DumpAsm extends GhidraScript {
	@Override
	public void run() throws Exception {
		String[] args = getScriptArgs();
		try (PrintWriter w = new PrintWriter(new FileWriter(new File(args[0])))) {
			for (int i = 1; i < args.length; i++) {
				Address a = toAddr(args[i]);
				Function f = getFunctionAt(a);
				if (f == null) {
					w.println("; no function at " + args[i]);
					continue;
				}
				w.println("; ==== " + f.getName() + " @ " + a);
				for (Instruction ins : currentProgram.getListing().getInstructions(f.getBody(), true)) {
					w.println(ins.getAddress() + "  " + ins.toString());
				}
			}
		}
	}
}
