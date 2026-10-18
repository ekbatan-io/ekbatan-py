#!/bin/zsh
# Area G: compile the codegen probe with the real annotation processor, then run it.
W=/Users/mike/workspace/ekbatan/.claude/worktrees/agent-aed9a04a1def50dee
J=/Users/mike/.sdkman/candidates/java/25.0.3-graal/bin
D=/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/G/codegen
CORE=$W/ekbatan-core/build/libs/ekbatan-core-1.1.2.jar
PROC=$W/ekbatan-annotation-processor/build/libs/ekbatan-annotation-processor-1.1.2.jar:/Users/mike/.gradle/caches/modules-2/files-2.1/com.squareup/javapoet/1.13.0/d6562d385049f35eb50403fa86bb11cce76b866a/javapoet-1.13.0.jar
rm -rf $D/out $D/gen && mkdir -p $D/out $D/gen
$J/javac -cp $CORE:$W/ekbatan-annotation-processor/build/libs/ekbatan-annotation-processor-1.1.2.jar -processorpath $PROC -s $D/gen -d $D/out $D/src/gprobe/CodegenParityProbe.java || exit 1
$J/java -cp $CORE:$D/out gprobe.CodegenParityProbe
