package nprobe;

import io.ekbatan.core.domain.GenericState;
import io.ekbatan.core.domain.Id;
import io.ekbatan.core.domain.Model;
import java.lang.Override;
import java.lang.String;
import java.math.BigDecimal;
import java.util.UUID;
import javax.annotation.processing.Generated;

@Generated("io.ekbatan.core.processor.AutoBuilderProcessor")
public final class WalletBuilder extends Model.Builder<Id<BuilderProbe.Wallet>, WalletBuilder, BuilderProbe.Wallet, GenericState> {
  UUID ownerId;

  BigDecimal balance;

  String memo;

  String label;

  private WalletBuilder() {
  }

  public WalletBuilder ownerId(UUID ownerId) {
    this.ownerId = ownerId;
    return this;
  }

  public UUID ownerId() {
    return this.ownerId;
  }

  public WalletBuilder balance(BigDecimal balance) {
    this.balance = balance;
    return this;
  }

  public BigDecimal balance() {
    return this.balance;
  }

  public WalletBuilder memo(String memo) {
    this.memo = memo;
    return this;
  }

  public String memo() {
    return this.memo;
  }

  public WalletBuilder label(String label) {
    this.label = label;
    return this;
  }

  public String label() {
    return this.label;
  }

  public static WalletBuilder wallet() {
    return new WalletBuilder();
  }

  @Override
  public BuilderProbe.Wallet build() {
    return new BuilderProbe.Wallet(this);
  }
}
