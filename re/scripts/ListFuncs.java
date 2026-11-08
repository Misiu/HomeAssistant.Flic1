// Ghidra headless post-script: print functions whose name matches a regex (case-insensitive).
// Args: <out.txt> <regex>
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.regex.Pattern;

public class ListFuncs extends GhidraScript {
	@Override
	public void run() throws Exception {
		String[] args = getScriptArgs();
		Pattern p = Pattern.compile(args[1], Pattern.CASE_INSENSITIVE);
		int total = 0, hit = 0;
		try (PrintWriter w = new PrintWriter(new FileWriter(new File(args[0])))) {
			for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
				total++;
				String n = f.getName(true);
				if (p.matcher(n).find()) {
					hit++;
					w.println(f.getEntryPoint() + "  " + n);
				}
			}
		}
		println("ListFuncs: " + hit + " of " + total + " functions matched");
	}
}
