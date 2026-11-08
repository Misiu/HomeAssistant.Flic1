// Ghidra headless post-script: decompile every function to one C file.
// Usage: analyzeHeadless <projdir> <proj> -import <bin> -scriptPath <this dir> -postScript DumpDecomp.java <out.c>
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;

public class DumpDecomp extends GhidraScript {
	@Override
	public void run() throws Exception {
		String[] args = getScriptArgs();
		if (args.length < 1) {
			printerr("need output path");
			return;
		}
		DecompInterface di = new DecompInterface();
		di.openProgram(currentProgram);
		int n = 0, failed = 0;
		try (PrintWriter w = new PrintWriter(new FileWriter(new File(args[0])))) {
			for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
				if (monitor.isCancelled()) break;
				DecompileResults r = di.decompileFunction(f, 60, monitor);
				w.println("// ==== " + f.getName() + " @ " + f.getEntryPoint() + " size=" + f.getBody().getNumAddresses());
				if (r.decompileCompleted()) {
					w.println(r.getDecompiledFunction().getC());
				} else {
					failed++;
					w.println("// FAILED: " + r.getErrorMessage());
				}
				n++;
			}
		}
		println("DumpDecomp: " + n + " functions, " + failed + " failed -> " + args[0]);
	}
}
